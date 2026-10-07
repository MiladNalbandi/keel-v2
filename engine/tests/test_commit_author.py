"""Who keel's commits are by: the person (Settings › Commit author, .keel/config.yml, the project's git name), with
Co-Authored-By: KeelBot at the end unless the setting is off; KeelBot itself only when nobody is known."""

import subprocess

import pytest
import yaml

from keel_engine.runtime import actions
from keel_engine.runtime.actions import COAUTHOR, ActionInput


@pytest.fixture(autouse=True)
def no_global_git(monkeypatch, tmp_path):
    # only the repo's own config counts: the machine's git name must not leak into the test
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True).stdout


def commit(repo, settings=None, name="x.py"):
    (repo / "src" / "scores" / name).write_text("x = 1\n")
    a = ActionInput(root=str(repo), phase="green", title="ranks", ac=None, acs=[], fake=True, flow="custom",
                    project="demo", settings=settings or {})
    r = actions.commit(a)
    assert r.ok, r.note
    return git(repo, "log", "-1", "--format=%an <%ae>%n%B").strip()


def test_the_projects_git_name_is_the_author_and_keelbot_the_co_author(repo):
    git(repo, "config", "user.name", "Ada Lovelace")
    git(repo, "config", "user.email", "ada@example.com")
    log = commit(repo)
    assert log.startswith("Ada Lovelace <ada@example.com>\nfeat: ranks")
    assert log.endswith("\n\n" + COAUTHOR) and COAUTHOR == "Co-Authored-By: KeelBot <keel.dev.bot@gmail.com>"


def test_the_setting_names_the_author_and_turns_the_keelbot_line_off(repo):
    git(repo, "config", "user.name", "Ada Lovelace")
    git(repo, "config", "user.email", "ada@example.com")
    log = commit(repo, {"commit_author": " Bo Smith <bo@example.com> ", "commit_coauthor": False})
    assert log.startswith("Bo Smith <bo@example.com>\n") and "KeelBot" not in log
    # a value that is not "Name <email>" is not a name: the project's git name again
    log = commit(repo, {"commit_author": "just a name"}, name="y.py")
    assert log.startswith("Ada Lovelace <ada@example.com>\n") and log.endswith(COAUTHOR)


def test_the_old_keelbot_author_in_config_and_the_images_placeholder_are_nobody(repo):
    cfg = repo / ".keel" / "config.yml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(yaml.safe_dump({"version": 4, "commit": {"author_name": "keelbot", "author_email": "keel.dev.bot@gmail.com"}}))
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "chore: config")
    git(repo, "config", "user.name", "keel")
    git(repo, "config", "user.email", "keel@localhost")        # the Docker image's system identity
    log = commit(repo)
    assert log.startswith("KeelBot <keel.dev.bot@gmail.com>\n") and COAUTHOR not in log    # KeelBot once, as the author
    # a project's own bot identity in config.yml still wins over the git name
    cfg.write_text(yaml.safe_dump({"version": 4, "commit": {"author_name": "ci-bot", "author_email": "ci@example.com"}}))
    log = commit(repo, name="y.py")
    assert log.startswith("ci-bot <ci@example.com>\n") and log.endswith(COAUTHOR)
