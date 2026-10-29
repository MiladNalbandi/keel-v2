"""keel 0.18.74 a flow that continues a branch with commits of its own (a pull request's branch) checks and reviews its
own work only: data.own_since is the flow's start, and verdict_actions.flow_base reads it (trace, audit, coverage, lint,
the full-diff review's range). A field report: such a flow reviewed, audited and traced all 41 commits of the branch."""

from conftest import start, wait
from test_v02 import commit_all, git, graph_values, write
from keel_engine.runtime import verdict_actions as va
from keel_engine.runtime.actions import ActionInput


def test_a_flow_on_a_branch_ahead_of_its_base_starts_its_own_work_at_its_start(client, repo):
    git(repo, "checkout", "-q", "-b", "feat/earlier-work")
    write(repo, "src/scores/old.py", "def old():\n    return 1\n")
    commit_all(repo, "feat: work done before this flow")
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    tid = start(client, repo)
    wait(client, tid)
    data = graph_values(client, tid)["data"]
    assert data["own_since"] == head
    a = ActionInput(root=str(repo), phase="ship", title="t", ac=None, acs=[], fake=True, flow="feature", data=data)
    assert va.flow_base(a, {}) == head
    files = va.branch_files(str(repo), va.flow_base(a, {}))
    assert "src/scores/old.py" not in files and "docs/specs/player-ranks.md" in files   # its own spec, not the earlier work


def test_a_flow_on_its_own_new_branch_has_no_own_since(client, repo):
    tid = start(client, repo)
    wait(client, tid)
    assert "own_since" not in graph_values(client, tid)["data"]
