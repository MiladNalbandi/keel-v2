"""Plan 5c: each agent chooses the project knowledge it uses (sections, code graph, memory, strict)."""

import json
from pathlib import Path

import pytest

from conftest import start, wait
from keel_engine import models
from keel_engine.runtime import agent_knowledge as ak, prompts
from keel_engine.workflows.estimate import estimate
from keel_engine.workflows.templates import get_template

ALL = ["architecture", "domain", "conventions", "data", "integrations", "journeys"]


def kb(root: Path, *sections: str) -> Path:
    d = root / "docs" / "knowledge"
    d.mkdir(parents=True, exist_ok=True)
    for s in sections:
        (d / f"{s}.md").write_text(f"# {s}\n".ljust(400, "x"))
    return d


def test_defaults_come_from_front_matter():
    # the code graph is opt-in: measured on ludus it did not save tokens (see tools/codegraph.py)
    assert ak.default_for("explorer") == {"sections": ALL, "code_graph": False, "memory": True, "strict": False}
    assert ak.default_for("test-author")["sections"] == ["domain", "conventions"]
    assert ak.default_for("implementer")["sections"] == ["architecture", "conventions", "data"]
    assert ak.default_for("security-auditor") == {"sections": ["data", "integrations"], "code_graph": False,
                                                  "memory": True, "strict": False}
    assert ak.default_for("librarian")["sections"] == []
    # no agent file: the builtin table, else the fallback
    assert ak.default_for("contract-author")["sections"] == ["domain", "integrations"]
    assert ak.default_for("contract-author")["code_graph"] is False
    assert ak.default_for("someone-new") == ak.FALLBACK


def test_the_thread_value_merges_over_the_default():
    sent = {"test-author": {"knowledge": {"sections": ["data", "nonsense"], "strict": True}}}
    k = ak.for_agent("test-author", sent)
    assert k == {"sections": ["data"], "code_graph": False, "memory": True, "strict": True}
    assert ak.for_agent("implementer", sent) == ak.default_for("implementer")
    assert ak.for_agent("implementer", {}) == ak.default_for("implementer")


def test_prompt_block_lists_exactly_the_allowed_files_that_exist(tmp_path):
    root = tmp_path / "p"
    d = kb(root, "architecture", "domain", "data")
    k = {**ak.for_agent("test-author", {}), "code_graph": True}   # domain, conventions; graph turned on
    p = prompts.task_prompt(agent="test-author", phase="red", step_name="red", title="t", root=str(root), ac=None, acs=[],
                            feedback=None, knowledge=k, graph=True)
    assert "Knowledge you can use" in p
    assert f"- {d / 'domain.md'}: " in p
    for other in ("architecture", "conventions", "data", "integrations", "journeys"):
        assert f"{other}.md" not in p
    assert "keel's memory of this project is in docs/knowledge/" not in p   # the generic line is replaced
    assert "codegraph_search" in p and "Memory:" in p
    # no graph server, or the graph turned off: no graph line
    assert "codegraph tools" not in prompts.task_prompt(agent="test-author", phase="red", step_name="red", title="t",
                                                        root=str(root), ac=None, acs=[], feedback=None, knowledge=k, graph=False)
    off = {**k, "code_graph": False, "memory": False}
    block = ak.prompt_block(str(root), off, True)
    assert "codegraph" not in block and "Memory:" not in block


def test_prompt_block_says_so_in_one_line_when_no_section_exists(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    k = {**ak.for_agent("test-author", {}), "code_graph": False, "memory": False}
    block = ak.prompt_block(str(root), k, False)
    assert block.count("\n") == 0 and "none of your sections (domain, conventions) exist" in block
    assert ak.prompt_block(str(root), {**k, "sections": []}, False) == ""


def test_the_librarian_keeps_its_own_instructions(tmp_path):
    root = tmp_path / "p"
    kb(root, "architecture")
    p = prompts.task_prompt(agent="librarian", phase="memory", step_name="m", title="t", root=str(root), ac=None, acs=[],
                            feedback=None, knowledge=ak.for_agent("librarian", {}), graph=True)
    assert "Knowledge you can use" not in p and "keel's memory of this project is in docs/knowledge/" in p


def test_token_cost_is_size_over_four(tmp_path):
    root = tmp_path / "p"
    kb(root, "domain", "data")   # 400 bytes each
    assert ak.tokens(str(root), ["domain", "conventions"]) == 100
    assert ak.tokens(str(root), ALL) == 200


def test_the_code_graph_server_is_given_only_with_the_flag():
    specs = [{"name": "keel", "command": "x"}, {"name": "codegraph", "command": "codegraph", "args": ["serve", "--mcp"]}]
    allow = ["mcp:keel:keel_next", "mcp:codegraph:*", "mcp:codegraph:codegraph_explore", "Read"]
    on_specs, on_allow = ak.filter_mcp(specs, allow, {"code_graph": True})
    assert on_specs == specs and on_allow == allow and ak.has_codegraph(on_specs, on_allow)
    off_specs, off_allow = ak.filter_mcp(specs, allow, {"code_graph": False})
    assert [s["name"] for s in off_specs] == ["keel"] and off_allow == ["mcp:keel:keel_next", "Read"]
    assert not ak.has_codegraph(off_specs, off_allow)
    # without a codegraph server (5b not there yet) nothing changes
    assert ak.filter_mcp(specs[:1], allow[:1], {"code_graph": False}) == (specs[:1], allow[:1])


def test_estimate_adds_knowledge_for_agents_without_history():
    wf = get_template("feature")
    base = estimate(wf, 2)
    more = estimate(wf, 2, knowledge_tokens={"test-author": 1000})
    assert more["knowledge_tokens"] > 0 and more["tokens"] == base["tokens"] + more["knowledge_tokens"]
    hist = [{"agent": "test-author", "tokens_in": 5000, "tokens_out": 500, "retries": 0}]
    assert estimate(wf, 2, hist, knowledge_tokens={"test-author": 1000})["knowledge_tokens"] == 0


@pytest.fixture
def seen(monkeypatch):
    """Every AgentRequest the flow makes, with the guard context file as it was when the agent ran."""
    out = []
    real = models.runner_for

    class Spy:
        def __init__(self, inner):
            self.inner = inner

        async def run(self, req, emit):
            out.append((req, json.loads(Path(req.guard_ctx).read_text())))
            return await self.inner.run(req, emit)

    monkeypatch.setattr(models, "runner_for", lambda m: Spy(real(m)))
    return out


def test_start_thread_knowledge_reaches_the_agent(client, repo, seen):
    kb(Path(repo), "domain", "data", "architecture")
    tid = start(client, repo, agents={"explorer": {"knowledge": {"sections": ["data"], "strict": True, "code_graph": False}}},
                mcp=[{"name": "codegraph", "command": "codegraph", "args": ["serve", "--mcp"]}])
    wait(client, tid)
    req, ctx = next((r, c) for r, c in seen if r.agent == "explorer")
    assert req.knowledge == {"sections": ["data"], "code_graph": False, "memory": True, "strict": True}
    assert f"- {Path(repo).resolve() / 'docs/knowledge/data.md'}: tables" in req.prompt
    assert "domain.md" not in req.prompt and "architecture.md" not in req.prompt
    assert [s["name"] for s in req.mcp_specs] == []          # code graph off: its server is not handed over
    assert ctx["knowledge_allowed"] == ["data"] and ctx["knowledge_strict"] is True
    assert req.toolbox.read_file("docs/knowledge/domain.md").startswith("REFUSED: knowledge section domain")


def test_unknown_sections_are_refused_at_start(client, repo):
    body = {"project_id": "demo", "root": str(repo), "workflow": get_template("feature").model_dump(), "title": "x",
            "agents": {"explorer": {"knowledge": {"sections": ["secrets"]}}}}
    assert client.post("/threads", json=body).status_code in (400, 422)
