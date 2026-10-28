"""0.18.65 the stale-base field report (docs/plans/field-report-clarify.md R2-R5): two remotes that disagree, a local base
behind one and ahead of the other. keel names one remote canonical (git.remote, else the base's upstream, else a remote
that has the base, else origin, else the only remote), compares with its copy of the base, says how far the local base
drifted, asks the person when base_branch names no local branch, leaves keel init's setup commits out of a branch's
change, and no agent changes .keel/config.yml, through the Edit tool or the shell."""
import subprocess
from pathlib import Path

import pytest

from keel_engine import hook, rules
from keel_engine.runtime import bases, branch_sync, container, flow_actions, init_actions, verdict_actions
from keel_engine.tools import git as g
from keel_engine.tools.agent_tools import ToolBox


def run(cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args], cwd=cwd,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def commit(cwd, name, text, msg):
    p = Path(cwd, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    run(cwd, "add", name)
    run(cwd, "commit", "-qm", msg)


def changed(root, base):
    """The committed files of `git diff <base>...HEAD` (what a review of the branch reads)."""
    return [f for f in run(root, "diff", "--name-only", f"{base}...HEAD").splitlines() if f]


CONFIG = "# keel's config\nbackend:\n  dir: .\nbase_branch: {base}\n\n# commands\ncommands:\n  api_test_ac: echo ok\n"


def config(repo, base="master", extra=""):
    Path(repo, ".keel").mkdir(exist_ok=True)
    Path(repo, ".keel/config.yml").write_text(CONFIG.format(base=base) + extra)


@pytest.fixture
def two(tmp_path):
    """`origin` (its master: A, O1) and `github` (its main: A, M1, M2, M3, where the newer work landed). The local master
    is A, O1, X1: one ahead of origin/master, and 2 ahead and 3 behind github/main. feat/x starts from github/main
    (0.18.64 start with base github/main) and adds one file."""
    seed = tmp_path / "seed"
    seed.mkdir()
    run(seed, "init", "-q", "-b", "master")
    commit(seed, "app.txt", "one\n", "A")
    origin = tmp_path / "origin.git"
    github = tmp_path / "github.git"
    run(tmp_path, "init", "-q", "--bare", "-b", "master", str(origin))
    run(tmp_path, "init", "-q", "--bare", "-b", "main", str(github))
    run(seed, "push", "-q", str(github), "master:main")
    commit(seed, "old/copy.txt", "an older copy\n", "O1")
    run(seed, "push", "-q", str(origin), "master")
    up = tmp_path / "up"
    run(tmp_path, "clone", "-q", str(github), str(up))
    for i in (1, 2, 3):
        commit(up, f"landed/m{i}.txt", f"{i}\n", f"M{i}")
    run(up, "push", "-q", "origin", "main")
    work = tmp_path / "work"
    run(tmp_path, "clone", "-q", "-o", "origin", str(origin), str(work))
    run(work, "remote", "add", "github", str(github))
    run(work, "fetch", "-q", "github")
    commit(work, "local.txt", "only here\n", "X1")
    run(work, "checkout", "-q", "--no-track", "-b", "feat/x", "github/main")
    commit(work, "feature.txt", "the change\n", "feat: the change")
    return work


def test_the_canonical_remote_is_the_bases_upstream_unless_the_config_names_one(two):
    root = str(two)
    config(two, "master")
    assert bases.canonical_remote(root) == "origin"            # local master tracks origin/master
    config(two, "master", "git:\n  remote: github\n")
    assert bases.canonical_remote(root) == "github"
    config(two, "main")                                        # no local main: the remote that has one
    assert bases.canonical_remote(root) == "github"
    config(two, "github/main")                                 # base_branch names the remote itself
    assert bases.canonical_remote(root) == "github"


def test_the_review_base_is_the_canonical_remotes_copy_and_the_drift_is_said(two):
    root = str(two)
    config(two, "master", "git:\n  remote: github\n")
    r = bases.resolve(root)
    assert (r["local"], r["remote"], r["ref"], r["ahead"], r["behind"]) == ("master", "github/main", "github/main", 2, 3)
    assert r["warning"] == "the local master is 3 commits behind and 2 ahead of github/main; keel compares with github/main"
    assert verdict_actions.base_ref(root, rules.load_config(root)) == "github/main"
    assert verdict_actions.base_drift(root, rules.load_config(root)).startswith("master is diverged from github/main")
    found = bases.branch_scope(root)
    assert found["scope"] == "git diff github/main...HEAD"
    assert changed(root, found["base"]) == ["feature.txt"]
    # the stale local base would have shown the other change's files as this branch's
    assert len(changed(root, "master")) == 4
    # the review flow's scope (the full-diff review reads the same)
    out = flow_actions.review_scope(type("A", (), {"root": root, "data": {}, "settings": {}})())
    assert out.ok and "git diff github/main...HEAD (1 file(s))" in out.note and "keel compares with github/main" in out.note


def test_base_branch_written_as_remote_branch_works_where_it_is_read(two):
    root = str(two)
    config(two, "github/main")
    r = bases.resolve(root)
    assert (r["name"], r["local"], r["remote"], r["ref"], r["explicit"]) == ("main", None, "github/main", "github/main", True)
    assert bases.missing(root) is None                         # named on purpose: nothing to ask
    assert verdict_actions.base_ref(root, rules.load_config(root)) == "github/main"
    assert branch_sync.default_onto(root, rules.load_config(root)) == "github/main"
    # a local feat/x stays a local branch name (only a real remote's name is cut off)
    assert bases.split("feat/x", ["origin", "github"]) == (None, "feat/x")
    assert bases.split("github/main", ["origin", "github"]) == ("github", "main")


def test_the_flow_start_report_offers_the_canonical_remotes_base(two):
    root = str(two)
    config(two, "master", "git:\n  remote: github\n")
    run(two, "checkout", "-q", "master")
    rep = branch_sync.report(root, branch_sync.fetch_all(root), g.head(root))
    assert "master is 3 commits behind github/main (github's default branch)" in rep["note"]
    assert "start it with base github/main" in rep["note"]


def test_a_base_branch_with_no_local_branch_asks_the_person(two, client):
    root = str(two)
    config(two, "main")
    gone = bases.missing(root)
    assert gone["remote"] == "github/main"
    assert [c["id"] for c in gone["choices"]] == ["remote", "track", "pick"]
    assert "no local branch main; github/main has it" in gone["text"]
    assert container.start_check(root)["base_missing"]["remote"] == "github/main"
    # the person's answers: keel writes the config, every other line stays
    bases.choose(root, "remote")
    text = Path(two, ".keel/config.yml").read_text()
    assert "base_branch: github/main\n" in text and text.startswith("# keel's config\n") and "# commands\n" in text
    bases.choose(root, "pick", "master")
    assert "base_branch: master\n" in Path(two, ".keel/config.yml").read_text()
    config(two, "main")
    r = client.post("/base", json={"root": root, "choice": "track"})
    assert r.status_code == 200, r.text
    assert run(two, "rev-parse", "--abbrev-ref", "main@{upstream}") == "github/main"
    assert "base_branch: main\n" in Path(two, ".keel/config.yml").read_text()
    assert r.json()["missing"] is None and r.json()["local"] == "main"
    r = client.post("/base", json={"root": root, "choice": "canonical", "remote": "github"})
    assert r.status_code == 200 and r.json()["canonical"] == "github"
    assert "git:\n  remote: github\n" in Path(two, ".keel/config.yml").read_text()
    assert client.post("/base", json={"root": root, "choice": "canonical", "remote": "nope"}).status_code == 400
    assert client.get("/base", params={"root": root}).json()["remotes"] == ["github", "origin"]


def test_no_agent_changes_the_base_through_the_edit_tool_or_the_shell(two):
    root = str(two)
    config(two, "main")
    writes = ["sed -i 's/base_branch: main/base_branch: master/' .keel/config.yml",
              "sed -i -e 's/main/master/' .keel/config.yml", "echo 'base_branch: master' >> .keel/config.yml",
              "perl -pi -e 's/main/master/' .keel/config.yml", "yq -i '.base_branch = \"master\"' .keel/config.yml",
              "cp /tmp/other.yml .keel/config.yml", "cat x | tee .keel/config.yml", "cd .keel && sed -i s/a/b/ config.yml",
              "python3 -c \"open('.keel/config.yml','w').write('x')\"", "rm -rf .keel/rules"]
    reads = ["cat .keel/config.yml", "grep base_branch .keel/config.yml", "sed -n 1,20p .keel/config.yml",
             "git log -- .keel/config.yml", "yq '.base_branch' .keel/config.yml", "cat .keel/config.yml > /tmp/copy.yml"]
    for phase in ("none", "green", "spec"):                     # KeelBot or a side session (no flow), and in a flow
        for cmd in writes:
            v = rules.check_bash(phase, cmd)
            assert not v.ok and v.bucket == "keel-rules", (phase, cmd)
        for cmd in reads:
            assert rules.check_bash(phase, cmd).ok, (phase, cmd)
    assert rules.check_bash("setup", "echo 'base_branch: main' > .keel/config.yml").ok     # init writes it
    # the hook (claude, opencode) and keel's own tools refuse it outside a flow too
    ctx = {"root": root, "phase": "none", "unlocks": []}
    assert "Agents never change their own rules" in hook.decide("Bash", {"command": writes[0]}, dict(ctx))
    assert hook.decide("Edit", {"file_path": str(two / ".keel/config.yml"), "old_string": "main", "new_string": "x"}, dict(ctx))
    assert hook.decide("Write", {"file_path": str(two / ".keel/config.yml"), "content": "x"}, dict(ctx))
    tb = ToolBox(root, "none")
    assert tb.run_command(writes[0]).startswith("REFUSED")
    assert tb.write_file(".keel/config.yml", "base_branch: master\n").startswith("REFUSED")
    assert "base_branch: main" in Path(two, ".keel/config.yml").read_text()


def test_init_setup_commits_are_not_the_branchs_change(two):
    root = str(two)
    run(two, "checkout", "-q", "--no-track", "-b", "keel/setup", "github/main")
    commit(two, ".keel/config.yml", "base_branch: github/main\n", "chore(setup): keel init")
    commit(two, "docs/RUNNING.md", "# running\n", "chore(setup): keel init")
    assert len(bases.setup_commits(root, "github/main")) == 2
    assert bases.own_base(root, "github/main") == ("github/main", [])     # only setup: the setup is the change
    run(two, "checkout", "-q", "-b", "feat/y")
    commit(two, "y.txt", "y\n", "feat: y")
    found = bases.branch_scope(root)
    assert found["compared"] == "github/main" and len(found["setup"]) == 2
    assert changed(root, found["base"]) == ["y.txt"]
    body = verdict_actions.pr_body(root, "p", {}, "y", "github/main")
    assert "## keel setup (not this change)" in body and "`.keel/config.yml`" in body and "`docs/RUNNING.md`" in body
    assert "- feat: y" in body and "- chore(setup)" not in body
    # a project commit style: keel's Keel-Ref line marks the setup commit
    assert bases.is_setup_commit("KEEL-1: chore: keel init\n\nKeel-Ref: chore(setup)\nCo-Authored-By: KeelBot <x@y>")
    assert not bases.is_setup_commit("feat: setup a thing")


def test_init_never_force_adds_what_gitignore_ignores_and_says_so(two):
    root = str(two)
    Path(two, ".gitignore").write_text("docs/RUNNING.md\nCLAUDE.md\n")
    commit(two, ".gitignore", Path(two, ".gitignore").read_text(), "ignore keel's files")
    Path(two, "docs").mkdir(exist_ok=True)
    Path(two, "docs/RUNNING.md").write_text("# running\n")
    Path(two, "CLAUDE.md").write_text("# rules\n")
    assert sorted(init_actions.ignored_setup_files(root)) == ["CLAUDE.md", "docs/RUNNING.md"]
    # keel's runtime files in .git/info/exclude are keel's own choice, not the project's: not listed
    g.exclude(root, ["ignored-by-keel.md"], "keel")
    Path(two, "ignored-by-keel.md").write_text("x")
    assert sorted(init_actions.ignored_setup_files(root)) == ["CLAUDE.md", "docs/RUNNING.md"]
    # nothing in keel's commit path force-adds
    src = "".join(p.read_text() for p in Path(verdict_actions.__file__).parent.glob("*.py"))
    assert '"add", "-f"' not in src and '"add", "--force"' not in src
