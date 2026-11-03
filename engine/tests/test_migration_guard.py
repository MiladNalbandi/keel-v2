"""keel 0.22.0 the migration guard protects migrations on the base branch only: a migration the current branch added may
still be corrected before it merges (issue #72)."""
import subprocess

from keel_engine import rules


def sh(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@x", "HOME": str(root), "PATH": "/usr/bin:/bin"})


def repo(tmp_path):
    root = tmp_path / "app"
    mig = root / "src/main/resources/db/migration"
    mig.mkdir(parents=True)
    sh(root, "init", "-q", "-b", "main")
    (mig / "V1__users.sql").write_text("create table users(id int);\n")
    sh(root, "add", "-A")
    sh(root, "commit", "-q", "-m", "init")
    sh(root, "switch", "-q", "-c", "feat/orders")
    (mig / "V2__orders.sql").write_text("create table orders(id int);\n")
    sh(root, "add", "-A")
    sh(root, "commit", "-q", "-m", "orders")
    return root, mig


def test_a_migration_on_the_base_stays_immutable(tmp_path):
    root, mig = repo(tmp_path)
    v = rules.check_edit("green", str(mig / "V1__users.sql"), {"root": str(root)}, exists=True)
    assert not v.ok and v.bucket == "migration" and "base branch" in v.reason


def test_a_migration_this_branch_added_may_be_corrected(tmp_path):
    root, mig = repo(tmp_path)
    assert rules.check_edit("green", str(mig / "V2__orders.sql"), {"root": str(root)}, exists=True).ok
    (mig / "V3__items.sql").write_text("create table items(id int);\n")   # new, not committed yet
    assert rules.check_edit("green", str(mig / "V3__items.sql"), {"root": str(root)}, exists=True).ok


def test_without_git_the_guard_stays_strict(tmp_path):
    mig = tmp_path / "src/main/resources/db/migration"
    mig.mkdir(parents=True)
    (mig / "V1__users.sql").write_text("x")
    assert not rules.check_edit("green", str(mig / "V1__users.sql"), {"root": str(tmp_path)}, exists=True).ok
