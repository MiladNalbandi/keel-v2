"""The Goals plugin's workflow on the fake model: the planner only reads, and its gate is a person's in every run mode."""

from __future__ import annotations

import pytest
import yaml
from conftest import PLUGIN, wait

from keel_engine import addons
from keel_engine.models import fake as fake_mod
from keel_engine.pluginhost import manifest as manifests
from keel_engine.workflows.model import load_yaml
from keel_engine.workflows.validate import validate

WORKFLOW = PLUGIN / "content" / "workflows" / "goals-plan.yaml"
PLAN = 'The plan.\n```json\n[{"title": "One", "request": "Do one", "workflow": "small"}]\n```'


def test_the_manifest_names_its_parts_and_tables():
    m = manifests.parse((PLUGIN / "keel-plugin.yml").read_text())
    assert m.name == "goals" and m.engine is None and m.content == "content" and m.migrations == "migrations"
    raw = yaml.safe_load((PLUGIN / "keel-plugin.yml").read_text())
    assert raw["permissions"]["tables"] == ["goals_goal", "goals_step"] and "secrets" not in raw["permissions"]


def test_the_workflow_is_valid_reads_only_and_starts_nothing():
    wf = load_yaml(WORKFLOW.read_text())
    assert validate(wf) == []
    by = {s.id: s for s in wf.steps}
    assert by["plan_gate"].person and by["plan_gate"].lock and by["plan"].phase == "review"
    assert not [a for s in wf.steps for a in s.actions()], "no code step: it commits, pushes and starts nothing"
    head = (PLUGIN / "content" / "agents" / "goals-planner.md").read_text().split("---")[1]
    assert "Write" not in yaml.safe_load(head)["tools"]
    assert "goals-planner" in {f.stem for f in addons.agent_files()}


def start(client, repo, mode="manual"):
    wf = load_yaml(WORKFLOW.read_text())
    r = client.post("/threads", json={
        "project_id": "demo", "root": str(repo), "workflow": wf.model_dump(), "title": "Plan: export", "request": "The goal: export",
        "models": {"default": {"provider": "fake", "mode": "api", "model": "fake"}},
        "settings": {"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause", "run_mode": mode}, "mcp": [], "skills": {}})
    assert r.status_code == 200, r.text
    return r.json()["thread_id"]


@pytest.mark.parametrize("mode", ["manual", "important", "auto"])
def test_the_plan_waits_for_a_person_in_every_mode(client, repo, monkeypatch, mode):
    real = fake_mod._plan
    monkeypatch.setattr(fake_mod, "_plan", lambda req: (None, "", PLAN, {}) if req.agent == "goals-planner" else real(req))
    s = wait(client, start(client, repo, mode))
    assert s["status"] == "waiting" and s["waiting"]["step"] == "plan_gate"
    assert '"title": "One"' in s["waiting"]["detail"]


def test_a_send_back_plans_again_with_the_persons_words_and_approve_ends_the_flow(client, repo, monkeypatch):
    prompts = []
    real = fake_mod._plan

    def plan(req):
        if req.agent == "goals-planner":
            prompts.append(req.prompt)
            return None, "", PLAN, {}
        return real(req)
    monkeypatch.setattr(fake_mod, "_plan", plan)
    tid = start(client, repo)
    wait(client, tid)
    client.post(f"/threads/{tid}/resume", json={"decision": "reject", "why": "fewer steps please"})
    s = wait(client, tid)
    assert s["waiting"]["step"] == "plan_gate" and len(prompts) == 2 and "fewer steps please" in prompts[1]
    client.post(f"/threads/{tid}/resume", json={"decision": "approve"})
    s = wait(client, tid)
    assert s["status"] == "done" and not s.get("children")
