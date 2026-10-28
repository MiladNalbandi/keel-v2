"""0.18.57 keel's commits follow the project's style: git.commit_template, else the ticket style of the branch's recent
commits; {type} from what changed, {ticket} from the flow or the branch, and the body never repeats the subject."""

import subprocess
from pathlib import Path

import pytest

from keel_engine.runtime import commit_style as cs
from keel_engine.runtime import verdict_actions as va
from keel_engine.runtime.actions import ActionInput, commit


def git(root, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, capture_output=True, text=True)


def write(root, rel, text="x\n"):
    f = Path(root) / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)


def commit_all(root, msg):
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", msg)


def project(tmp_path, config="version: 4\n", branch="feature/SHOPS-1234-euro-prices"):
    root = tmp_path / "shop"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(root, ".keel/config.yml", config)
    commit_all(root, "chore: start")
    git(root, "checkout", "-q", "-b", branch)
    return root


def keel_commit(root, phase, ac=None, flow="feature", title="Show prices in euros", **kw):
    a = ActionInput(root=str(root), phase=phase, title=title, ac=ac, acs=[ac] if ac else [], fake=False, flow=flow, **kw)
    r = commit(a)
    assert r.ok, (r.note, r.detail)
    return git(root, "log", "-1", "--format=%s").stdout.strip(), git(root, "log", "-1", "--format=%b").stdout


def test_render_drops_missing_placeholders_with_their_punctuation():
    assert cs.render("{ticket}: {type}: {subject}", "feat", "AC-1", "euro prices", "SHOPS-1234") == "SHOPS-1234: feat: euro prices"
    assert cs.render("{ticket}: {type}: {subject}", "feat", "AC-1", "euro prices", None) == "feat: euro prices"
    assert cs.render("[{ticket}] {type}({scope}): {subject}", "test", "", "x", "SHOPS-1") == "[SHOPS-1] test: x"


def test_type_follows_what_changed():
    assert cs.conventional_type("ac", ["api-test", "web-test"], ["a_test.php"]) == "test"
    assert cs.conventional_type("green", ["api-main"], ["src/A.php"], refactor=True) == "refactor"
    assert cs.conventional_type("green", ["api-main"], ["src/A.php"], flow="fix") == "fix"
    assert cs.conventional_type("ac", ["other"], ["README.md", "docs/x.md"]) == "docs"
    assert cs.conventional_type("green", ["api-main", "api-test"], ["src/A.php", "tests/ATest.php"]) == "feat"


def test_ticket_from_the_branch_name_only_when_it_is_a_part_of_it():
    assert cs.ticket_from_branch("feature/SHOPS-1234-euro-prices") == "SHOPS-1234"
    assert cs.ticket_from_branch("SHOPS-77_fix") == "SHOPS-77"
    assert cs.ticket_from_branch("keel/player-ranks-2") is None
    assert cs.ticket_from_branch("feat/AC-1-x") is None


def test_inferred_only_from_a_ticket_style_the_team_shares():
    team = ["SHOPS-1: feat: a", "SHOPS-2: fix: b", "SHOPS-3: feat: c", "Merge stuff"]
    assert cs.infer(team) == "{ticket}: {type}: {subject}"
    assert cs.infer(["[SHOPS-1] add a", "[SHOPS-2] add b", "[SHOPS-3] c"]) == "[{ticket}] {subject}"
    assert cs.infer(["feat: a", "fix: b", "chore: c"]) is None        # keel's type(scope) is conventional already
    assert cs.infer(["SHOPS-1: feat: a", "x", "y", "z", "w"]) is None


def test_the_configured_template_with_the_branch_ticket_and_keel_ref(tmp_path):
    root = project(tmp_path, "version: 4\ngit:\n  commit_template: '{ticket}: {type}: {subject}'\n")
    write(root, "tests/PriceTest.php")
    subject, body = keel_commit(root, "red", {"id": "AC-1", "title": "prices show in euros"})
    assert subject == "SHOPS-1234: test: prices show in euros"
    assert "Keel-Ref: test(AC-1)" in body
    write(root, "src/Price.php")
    subject, body = keel_commit(root, "green", {"id": "AC-1", "title": "prices show in euros"})
    assert subject == "SHOPS-1234: feat: prices show in euros" and "Keel-Ref: feat(AC-1)" in body
    # the trace still links the criterion through Keel-Ref
    shas = va.ac_commits(str(root), "AC-1", "main")
    assert shas["red"] and shas["green"] and shas["red"] != shas["green"]
    # 0.18.64 and so does the AC gate (it said "no test(AC-1) commit found" for these two)
    from keel_engine.runtime import flow_actions
    gate = flow_actions.ac_evidence(str(root), {"id": "AC-1", "title": "prices show in euros"}, [], None)
    assert "no `test(AC-1)` commit found" not in gate and "no `feat(AC-1)` commit found" not in gate
    assert "SHOPS-1234: test: prices show in euros" in gate and "tests/PriceTest.php" in gate
    # ship's doc commit follows it too (item C), with the flow's ticket from data
    write(root, "README.md", "euros\n")
    subject, _ = keel_commit(root, "close", title="Show prices in euros", data={"ticket": "SHOPS-1234"})
    assert subject == "SHOPS-1234: docs: Show prices in euros"


def test_the_style_is_inferred_from_the_team_and_keels_own_commits_do_not_count(tmp_path):
    root = project(tmp_path)
    for n, msg in enumerate(["SHOPS-1200: feat: cart", "SHOPS-1201: fix: totals", "SHOPS-1202: feat: coupons"]):
        write(root, f"src/f{n}.php")
        commit_all(root, msg)
    write(root, "src/Price.php")
    subject, body = keel_commit(root, "ac", flow="quick", title="Show prices in euros")
    assert subject == "SHOPS-1234: feat: Show prices in euros" and "Keel-Ref: feat" in body
    assert cs.recent_subjects(str(root))[0] == "SHOPS-1202: feat: coupons"      # keel's own commit is left out


def test_keels_own_form_stays_without_a_style_and_a_cut_subject_never_repeats_in_the_body(tmp_path):
    root = project(tmp_path, branch="keel/player-ranks")
    write(root, "src/Rank.php")
    long = "For a player with at least one recorded score the rank is the 1-based position of the best score"
    subject, body = keel_commit(root, "green", {"id": "AC-2", "title": long})
    assert subject.startswith("feat(AC-2): For a player") and subject.endswith("…") and len(subject) <= 72
    assert "Keel-Ref" not in body
    first = body.strip().splitlines()[0]
    assert first.startswith("…") and subject[len("feat(AC-2): "):-1] not in first


def test_init_commits_its_own_files_and_leaves_a_fixs_changes_for_the_person(tmp_path):
    # 0.18.64 a rung fix changed a lock file: the whole setup commit was refused, again on every "try again"
    root = project(tmp_path)
    write(root, "deps.lock", "widget = 1\n")
    commit_all(root, "chore: the lock")
    write(root, "deps.lock", "widget = 2\n")
    write(root, "docs/RUNNING.md", "# Running\n")
    a = ActionInput(root=str(root), phase="setup", title="keel init", ac=None, acs=[], fake=False, flow="init")
    r = commit(a)
    assert r.ok and "left uncommitted for you (not init's files): deps.lock" in r.note, (r.note, r.detail)
    assert git(root, "show", "--name-only", "--format=", "HEAD").stdout.split() == ["docs/RUNNING.md"]
    assert "deps.lock" in git(root, "status", "--porcelain").stdout

