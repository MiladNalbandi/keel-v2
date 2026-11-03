"""The Answers plugin: its manifest and package, its content as keel loads it, and the flow on the fake model. The rule
that matters most: each answer waits for a person, in every run mode, and only what the person approved reaches the
document."""

from __future__ import annotations

import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml
from conftest import PLUGIN, wait

from keel_engine import addons
from keel_engine.models import fake as fake_mod
from keel_engine.pluginhost import manifest as manifests
from keel_engine.runtime import run_mode
from keel_engine.workflows.model import from_dict, load_yaml
from keel_engine.workflows.validate import validate

ROOT = PLUGIN.parents[1]
WORKFLOW = PLUGIN / "content" / "workflows" / "answers-questionnaire.yaml"
DOC = "docs/answers/northwind-security-review.md"
QUESTIONS = ["How do you store passwords?", "Do you hold a security certificate?"]


def test_the_manifest_has_only_a_content_part():
    m = manifests.parse((PLUGIN / "keel-plugin.yml").read_text())
    assert m.name == "answers" and m.engine is None and m.api is None and m.web is None and m.migrations is None
    assert m.content == "content"


def test_keel_loads_its_workflow_and_agents_from_the_plugin():
    names = {f.stem for f in addons.agent_files()}
    assert {"answers-reader", "answers-drafter", "answers-checker", "answers-writer"} <= names
    assert addons.refused("answers") == []


def test_the_workflow_is_valid_and_every_approval_is_a_persons():
    wf = load_yaml(WORKFLOW.read_text())
    assert validate(wf) == []
    by = {s.id: s for s in wf.steps}
    assert by["approve"].person and by["approve"].for_each == "checked" and by["approve"].lock
    assert by["final"].person and by["final"].lock
    # agents before the approvals cannot write: phase review denies every file, and they have no Write tool
    for sid in ("read", "draft", "check"):
        assert by[sid].phase == "review"
    for agent in ("answers-reader", "answers-drafter", "answers-checker"):
        head = (PLUGIN / "content" / "agents" / f"{agent}.md").read_text().split("---")[1]
        assert "Write" not in yaml.safe_load(head)["tools"]
    # nothing that pushes, opens a PR or sends anything
    assert not {a for s in wf.steps for a in s.actions()} & {"pr", "open_pr", "push_check"}


@pytest.mark.parametrize("mode", ["manual", "important", "auto", "readonly"])
def test_no_run_mode_answers_a_person_gate(mode):
    wf = load_yaml(WORKFLOW.read_text())
    step = next(s for s in wf.steps if s.id == "final")
    q = {"step": "final", "kind": "gate", "title": "the whole document", "options": ["approve", "reject"]}
    kind = run_mode.classify(q, step, flow=wf.id)
    assert kind == "note"
    assert run_mode.decide(mode, kind, q) is None


def test_the_supervisor_never_looks_at_a_person_gate():
    # plugins/supervisor policy.NEVER holds "note"; keel's table says the same for every mode
    assert "note" in run_mode.SAFETY


def test_an_older_keel_leaves_it_out_because_of_requires_keel():
    # an older keel would not know the key person: true; the resolver reads this and leaves the plugin out, with the reason
    m = manifests.parse((PLUGIN / "keel-plugin.yml").read_text())
    assert m.keel == ">=0.20.2"
    assert addons.satisfies("0.20.2", m.keel) and not addons.satisfies("0.20.1", m.keel)


def test_person_belongs_to_a_gate():
    wf = from_dict({"name": "x", "keel_rules": False, "steps": [
        {"id": "a", "kind": "agent", "name": "a", "agent": "explorer", "person": True}]})
    assert any("only a gate takes person" in e for e in validate(wf))


# ------------------------------------------------------------------ the flow

def script(monkeypatch, *, drop_second=False):
    """The fake model as the four agents answer; answers-writer really writes the document."""
    real = fake_mod._plan
    written: list[dict] = []

    def plan(req):
        item = req.item or {}
        if req.agent == "answers-reader":
            rows = [{"title": q, "doc": DOC} for q in QUESTIONS]
            return None, "", "Two questions.\n```json\n" + json.dumps(rows) + "\n```", {}
        if req.agent == "answers-drafter":
            row = {"title": item.get("title"), "doc": item.get("doc"), "answer": f"Draft for {item.get('title')}",
                   "sources": ["src/scores/__init__.py:1 (score)"], "sureness": "sure"}
            return None, "", "```json\n" + json.dumps([row]) + "\n```", {}
        if req.agent == "answers-checker":
            row = {**{k: item.get(k) for k in ("title", "doc", "answer", "sources", "sureness")}, "check": "ok",
                   "show": f"**Answer** (sure):\n\n{item.get('answer')}\n\n**Sources**\n\n- {item['sources'][0]}\n\n**Check:** ok"}
            return None, "", "```json\n" + json.dumps([row]) + "\n```", {}
        if req.agent == "answers-writer":
            written.append(dict(item))
            root = Path(req.root)
            path = root / DOC
            path.parent.mkdir(parents=True, exist_ok=True)
            before = path.read_text() if path.exists() else "# Answers\n"
            if item:
                before += f"\n## {item.get('title')}\n\n{item.get('answer')}\n\n- {item['sources'][0]}\n"
            return DOC, before, DOC, {}
        return real(req)
    monkeypatch.setattr(fake_mod, "_plan", plan)
    return written


def start(client, repo, mode="manual"):
    wf = load_yaml(WORKFLOW.read_text())
    r = client.post("/threads", json={
        "project_id": "demo", "root": str(repo), "workflow": wf.model_dump(), "title": "Northwind security review",
        "request": "\n".join(QUESTIONS),
        "models": {"default": {"provider": "fake", "mode": "api", "model": "fake"}},
        "settings": {"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause", "run_mode": mode}, "mcp": [], "skills": {}})
    assert r.status_code == 200, r.text
    return r.json()["thread_id"]


def answer(client, tid, decision="approve", why=None, **payload):
    r = client.post(f"/threads/{tid}/resume", json={"decision": decision, "why": why, "payload": payload})
    assert r.status_code == 200, r.text
    return wait(client, tid)


def test_each_answer_waits_shows_its_source_and_only_approved_ones_are_written(client, repo, monkeypatch):
    written = script(monkeypatch)
    tid = start(client, repo)
    s = wait(client, tid)
    assert s["status"] == "waiting" and s["waiting"]["step"] == "approve"
    assert QUESTIONS[0] in s["waiting"]["detail"]
    assert "**Sources**" in s["waiting"]["detail"] and "src/scores/__init__.py:1" in s["waiting"]["detail"]
    assert [c["name"] for c in s["waiting"]["choice_info"]] == ["approve", "change", "drop"]
    assert not (repo / DOC).exists(), "nothing is written before a person approved it"

    s = answer(client, tid)                                  # the first answer: approved, written
    assert s["waiting"]["step"] == "approve" and QUESTIONS[1] in s["waiting"]["detail"]
    assert QUESTIONS[0] in (repo / DOC).read_text()

    refused = client.post(f"/threads/{tid}/resume", json={"decision": "approve", "payload": {"choice": "drop"}})
    assert refused.status_code >= 400 or wait(client, tid)["status"] == "waiting", "drop needs a reason"
    s = answer(client, tid, why="no certificate", choice="drop")     # the second: dropped
    assert s["waiting"]["step"] == "final"
    assert QUESTIONS[0] in s["waiting"]["detail"], "the last gate shows the document"
    text = (repo / DOC).read_text()
    assert QUESTIONS[1] not in text
    assert len([w for w in written if w.get("title")]) == 1

    s = answer(client, tid)
    assert s["status"] == "done", s
    log = subprocess.run(["git", "-C", str(repo), "log", "--name-only", "-1", "--format=%s"], capture_output=True, text=True).stdout
    assert DOC in log


@pytest.mark.parametrize("mode", ["important", "auto"])
def test_a_run_mode_that_approves_by_itself_still_stops_at_each_answer(client, repo, monkeypatch, mode):
    script(monkeypatch)
    tid = start(client, repo, mode)
    s = wait(client, tid)
    assert s["status"] == "waiting" and s["waiting"]["step"] == "approve", s
    assert not (repo / DOC).exists()
    s = answer(client, tid)
    assert s["waiting"]["step"] == "approve"
    s = answer(client, tid)
    assert s["status"] == "waiting" and s["waiting"]["step"] == "final", "the last look is a person's too"


def test_change_gives_the_writer_the_persons_note(client, repo, monkeypatch):
    written = script(monkeypatch)
    tid = start(client, repo)
    wait(client, tid)
    answer(client, tid, why="say bcrypt", choice="change")
    first = next(w for w in written if w.get("title") == QUESTIONS[0])
    assert first["decision"] == "change" and first["reason"] == "say bcrypt"


def test_a_request_with_no_questions_stops_and_says_so(client, repo, monkeypatch):
    real = fake_mod._plan
    monkeypatch.setattr(fake_mod, "_plan", lambda req: (None, "", "No questions.\n```json\n[]\n```", {})
                        if req.agent == "answers-reader" else real(req))
    tid = start(client, repo)
    s = wait(client, tid)
    assert s["status"] == "waiting" and s["waiting"]["step"] == "no_questions"
    s = answer(client, tid, choice="stop")
    assert s["status"] == "done"
    assert not (repo / DOC).exists()


@pytest.fixture
def packed(tmp_path):
    subprocess.run(["bash", str(ROOT / "scripts" / "build-plugin.sh"), str(PLUGIN), str(tmp_path), "--no-build"],
                   env=dict(os.environ), check=True, capture_output=True)
    return tmp_path


def test_the_package_holds_content_only(packed):
    with tarfile.open(packed / "answers-0.1.0.kplug") as t:
        names = t.getnames()
    assert "content/workflows/answers-questionnaire.yaml" in names and "keel-plugin.yml" in names and "files.sha256" in names
    assert not [n for n in names if n.startswith(("engine", "api", "web"))]
