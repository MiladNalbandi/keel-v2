"""The Org plugin (issue #92): the org's YAML and its link checks, the routes, and whole missions on the fake model -
a lead plans, gives work to its team, the team's flows run, the lead reviews and you accept or send the report back."""

from pathlib import Path

import pytest
import yaml
from conftest import mission, thread, wait_for, wait_status, wait_thread
from keel_engine.runtime.actions import ActionInput

from keel_plugin_org import VERSION, actions, config, store, templates

PROPOSE = config.EXAMPLE.replace("projects: [shop]", "projects: [demo]")
ORG = PROPOSE.replace("autonomy: propose", "autonomy: act")


def put(client, oid="shop", text=ORG):
    r = client.put(f"/org/orgs/{oid}", json={"yaml": text})
    assert r.status_code == 200, r.text
    return r.json()


def assign(client, oid="shop", **kw):
    body = {"role": "lead", "project_id": "demo", "title": "Cart count in the header",
            "request": "Show how many items are in the cart, next to the cart icon.", **kw}
    r = client.post(f"/org/orgs/{oid}/missions", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def answer(client, tid, choice, why=""):
    r = client.post(f"/threads/{tid}/resume", json={"decision": "approve", "why": why, "payload": {"choice": choice}})
    assert r.status_code == 200, r.text


def review_thread(client, mid):
    v = mission(client, mid)
    tids = [f["thread_id"] for f in v["flows"] if f["stage"] == "review"]
    return tids[-1] if tids else None


# ------------------------------------------------------------------ the org's YAML

def test_version_matches_the_manifest():
    manifest = yaml.safe_load((Path(__file__).resolve().parents[2] / "keel-plugin.yml").read_text())
    assert manifest["version"] == VERSION


def test_the_example_is_a_valid_org_and_its_links_answer():
    org = config.parse(config.EXAMPLE)
    assert set(org.roles) == {"me", "lead", "fe", "be"}
    assert config.team(org, "lead") == ["fe", "be"]
    assert config.team(org, "me") == ["lead"]
    assert config.boss(org, "fe") == "lead" and config.boss(org, "lead") == "me" and config.boss(org, "me") is None
    assert config.may(org, "lead", "fe", "delegate")
    assert not config.may(org, "fe", "lead", "delegate")      # work only goes down a delegate link
    assert config.may(org, "fe", "lead", "ask")
    assert config.humans(org) == ["me"]


@pytest.mark.parametrize("text,needle", [
    ("roles: {a: {workflows: [small]}}\nlinks: [{from: a, to: ghost, can: [delegate]}]", "there is no role ghost"),
    ("roles: {me: {human: true}, a: {workflows: [small]}}\nlinks: [{from: a, to: me, can: [delegate]}]", "a human role"),
    ("roles: {a: {workflows: [small], plan: big}}", "big is not in its workflows"),
    ("roles: {a: {}}", "name the workflows"),
    ("roles: {Bad Name: {workflows: [small]}}", "a role name is lowercase"),
    ("roles: {a: {workflows: [small], autonomy: maybe}}", "roles.a.autonomy"),
    ("roles: {a: {workflows: [small]}}\nlinks: [{from: a, to: a, can: [boss]}]", "links.0.can"),
    ("roles: [", "not valid YAML"),
    ("just text", "a YAML mapping"),
])
def test_an_org_with_problems_names_each_one(text, needle):
    with pytest.raises(config.OrgError) as e:
        config.parse(text)
    assert any(needle in p for p in e.value.problems), e.value.problems


def test_any_shape_is_config_a_peer_link_and_a_matrix():
    org = config.parse("""
roles:
  me: {human: true}
  pm: {workflows: [spike]}
  lead: {workflows: [lead-intake]}
  qa: {workflows: [review-pr]}
links:
  - {from: me, to: [pm, lead], can: [delegate]}
  - {from: [pm, lead], to: qa, can: [delegate, ask]}
  - {from: pm, to: lead, can: [ask]}
""")
    assert config.team(org, "me") == ["pm", "lead"]
    assert config.may(org, "pm", "qa", "delegate") and config.may(org, "lead", "qa", "delegate")
    assert config.may(org, "pm", "lead", "ask") and not config.may(org, "pm", "lead", "delegate")


# ------------------------------------------------------------------ routes

def test_save_check_and_list_orgs(client):
    bad = client.put("/org/orgs/shop", json={"yaml": "roles: {a: {}}"})
    assert bad.status_code == 400 and "name the workflows" in bad.json()["error"]
    assert client.post("/org/check", json={"yaml": "roles: {a: {}}"}).json()["ok"] is False
    assert client.post("/org/check", json={"yaml": ORG}).json() == {"ok": True, "problems": [], "roles": ["me", "lead", "fe", "be"]}
    v = put(client)
    assert v["name"] == "Shop team" and v["problems"] == []
    lead = next(r for r in v["roles"] if r["id"] == "lead")
    assert lead["team"] == ["fe", "be"] and lead["boss"] == "me" and lead["autonomy"] == "act"
    assert [o["id"] for o in client.get("/org/orgs").json()] == ["shop"]
    assert client.get("/org/orgs/nope").status_code == 404
    assert client.put("/org/orgs/Bad%20Id", json={"yaml": ORG}).status_code == 400
    assert "roles:" in client.get("/org/example").json()["yaml"]


def test_assign_checks_the_role_the_link_the_workflow_and_the_project(client):
    put(client)
    def refused(**kw):
        r = client.post("/org/orgs/shop/missions", json={"role": "lead", "project_id": "demo", "title": "t", **kw})
        assert r.status_code == 400, r.text
        return r.json()["error"]
    assert "no role" in refused(role="cto")
    assert "is a person" in refused(role="me")
    assert "You do not give work to fe" in refused(role="fe")           # the person's links say who they lead
    assert "may not run" in refused(workflow="fix")
    other = ORG.replace("projects: [demo]", "projects: [elsewhere]")
    put(client, text=other)
    assert "does not work on demo" in refused()


# ------------------------------------------------------------------ whole missions

def test_a_lead_plans_its_team_works_the_lead_reviews_and_you_accept(client):
    put(client)
    m = assign(client, source="SHOP-142", cap_tokens=400_000)
    assert m["status"] in ("queued", "running") and m["stage"] == "plan" and "run_key" not in m
    v = wait_status(client, m["id"], "running")
    # the lead's review waits at the report gate: only a person answers it
    tid = wait_for(lambda: review_thread(client, m["id"]), what="the review flow")
    s = wait_for(lambda: (lambda x: x if x.get("waiting") else None)(thread(client, tid)), what="the report gate")
    assert s["waiting"]["step"] == "accept"
    assert s["waiting"]["detail"].startswith("The team finished") and "Checked:\n- ✓ fe: checked the diff" in s["waiting"]["detail"]
    v = mission(client, m["id"])
    kids = v["mission"]["children"]
    assert [k["role"] for k in kids] == ["fe", "be"]
    assert all(k["status"] == "reported" and k["stage"] == "work" and k["workflow"] == "small" for k in kids)
    # the lead's budget, less its plan so far and a fifth kept for its review, split between its team
    assert all(k["cap_tokens"] == kids[0]["cap_tokens"] for k in kids)
    assert 100_000 < kids[0]["cap_tokens"] < 160_000 and sum(k["cap_tokens"] for k in kids) <= 400_000 * 4 // 5
    assert v["mission"]["report"]["summary"].startswith("The team finished")
    # how each flow was started: the lead's own folder, the team in worktrees, the run mode from the role's autonomy
    lead_start = client.started[0]
    assert lead_start["workflow_id"] == "lead-intake" and lead_start["run_mode"] == "auto" and "where" not in lead_start
    assert "Source: SHOP-142" in lead_start["request"] and "- fe: Frontend engineer; workflows: small, feature, fix" in lead_start["request"]
    assert lead_start["options"]["ticket"] == "SHOP-142"
    team = [s for s in client.started if s["workflow_id"] == "small"]
    assert len(team) == 2 and all(s["where"] == "worktree" and s["force"] is True for s in team)
    review = next(s for s in client.started if s["workflow_id"] == "lead-review")
    assert "## fe: " in review["request"] and "## be: " in review["request"]
    answer(client, tid, "accept", "Looks good.")
    v = wait_status(client, m["id"], "done")
    assert all(k["status"] == "done" for k in v["mission"]["children"])
    assert v["mission"]["decision"] == {"choice": "accept", "why": "Looks good."}
    kinds = [h["kind"] for h in v["history"]]
    assert kinds[0] == "made" and "delegated" in kinds and "review" in kinds and kinds[-1] == "done"
    assert v["rollup"]["counts"] == {"done": 2}


def test_send_back_plans_again_with_your_note_until_the_limit(client):
    put(client, text=ORG.replace("send_backs: 3", "send_backs: 1"))
    m = assign(client)
    tid = wait_for(lambda: review_thread(client, m["id"]), what="the review flow")
    wait_for(lambda: thread(client, tid).get("waiting"), what="the report gate")
    answer(client, tid, "send_back", "Use the style guide's green.")
    # planned again: a second lead-intake whose request carries the note, and a second round of team missions
    wait_for(lambda: sum(1 for s in client.started if s["workflow_id"] == "lead-intake") == 2, what="the second plan")
    again = [s for s in client.started if s["workflow_id"] == "lead-intake"][1]
    assert "Use the style guide's green." in again["request"] and "(1 of 1)" in again["request"]
    tid2 = wait_for(lambda: (lambda t: t if t and t != tid else None)(review_thread(client, m["id"])), what="the second review")
    wait_for(lambda: thread(client, tid2).get("waiting"), what="the second report gate")
    v = mission(client, m["id"])
    assert v["mission"]["send_backs"] == 1 and len(v["mission"]["children"]) == 4
    answer(client, tid2, "send_back", "Still not right.")
    # the mission and its history are two reads: wait until both say the limit was reached
    v = wait_for(lambda: (lambda x: x if x["mission"]["status"] == "reported" and "limit" in [h["kind"] for h in x["history"]]
                          else None)(mission(client, m["id"])), what="the send-back limit")
    assert "Sent back 1 times already" in v["mission"]["note"]
    # you can still accept it by hand
    r = client.post(f"/org/missions/{m['id']}/accept", json={"why": "fine, I will fix the colour"})
    assert r.status_code == 200 and r.json()["status"] in ("reported", "done")
    assert wait_status(client, m["id"], "done")


def test_max_running_keeps_the_team_in_a_queue(client):
    put(client, text=ORG.replace("max_running: 3", "max_running: 1"))
    m = assign(client)
    tid = wait_for(lambda: review_thread(client, m["id"]), what="the review flow")
    wait_for(lambda: thread(client, tid).get("waiting"), what="the report gate")
    # one flow at a time: each team member started only after the one before it ended
    hist = mission(client, m["id"])["mission"]["children"]
    first, second = sorted(hist, key=lambda k: k["created_at"])
    t1 = thread(client, first["thread_id"])
    assert t1["status"] == "done" and second["thread_id"]


def test_a_lead_with_no_team_work_reports_from_its_plan(client, monkeypatch):
    monkeypatch.setenv("KEEL_FAKE_ORG_CHILDREN", "0")
    put(client)
    m = assign(client)
    v = wait_status(client, m["id"], "reported")
    assert v["mission"]["children"] == [] and "Split Cart count in the header into 0 part(s)." in v["mission"]["report"]["summary"]
    assert client.post(f"/org/missions/{m['id']}/accept", json={}).json()["status"] in ("reported", "done")
    wait_status(client, m["id"], "done")


def test_a_watching_role_waits_for_you_to_start_it(client):
    put(client, text=ORG.replace("    autonomy: act\n  fe:", "    autonomy: watch\n  fe:", 1))
    m = assign(client)
    assert m["status"] == "proposed" and client.started == []
    assert client.post(f"/org/missions/{m['id']}/start").json()["status"] in ("queued", "running")
    wait_for(lambda: client.started, what="the start")


def test_propose_asks_you_at_the_lead_plan(client):
    put(client, text=PROPOSE)
    m = assign(client)
    wait_status(client, m["id"], "running")
    tid = wait_for(lambda: mission(client, m["id"])["mission"]["thread_id"], what="the plan flow")
    s = wait_for(lambda: thread(client, tid).get("waiting"), what="the plan gate")
    assert s["step"] == "plan_gate" and client.started[0]["run_mode"] == "manual"
    # the gate shows exactly what will be given out, checked against the org
    detail = s["detail"]
    assert "It gives out 2 mission(s):" in detail and "1. Frontend engineer (fe) · small" in detail and "2. Backend engineer (be) · small" in detail
    assert mission(client, m["id"])["mission"]["children"] == []        # nothing given out before you approve
    r = client.post(f"/threads/{tid}/resume", json={"decision": "approve"})
    assert r.status_code == 200
    wait_for(lambda: len(mission(client, m["id"])["mission"]["children"]) == 2, what="the team's missions")


DEEP = """\
name: Deep
projects: [demo]
roles:
  me: { human: true }
  cto: { title: CTO, agent: org-tech-lead, workflows: [lead-intake, lead-review], plan: lead-intake, review: lead-review, autonomy: act }
  lead: { title: Web lead, agent: org-tech-lead, workflows: [lead-intake, lead-review], plan: lead-intake, review: lead-review, autonomy: act }
  dev: { title: Web engineer, agent: implementer, workflows: [small], autonomy: act }
links:
  - { from: me, to: cto, can: [delegate] }
  - { from: cto, to: lead, can: [delegate] }
  - { from: lead, to: dev, can: [delegate] }
  - { from: dev, to: lead, can: [report] }
  - { from: lead, to: cto, can: [report] }
  - { from: cto, to: me, can: [report] }
"""


def test_a_lead_in_the_middle_reports_to_its_boss_and_only_the_top_asks_you(client):
    put(client, "deep", DEEP)
    m = assign(client, "deep", role="cto")
    tid = wait_for(lambda: review_thread(client, m["id"]), timeout=60, what="the CTO's review")
    s = wait_for(lambda: (lambda x: x if x.get("waiting") else None)(thread(client, tid)), what="the CTO's report gate")
    assert s["waiting"]["step"] == "accept"
    lead = mission(client, m["id"])["mission"]["children"][0]
    assert lead["role"] == "lead" and lead["status"] == "reported" and lead["children"][0]["status"] == "reported"
    # the lead's own review went through its report gate without asking anyone: its report went up to the CTO
    lead_review = review_thread(client, lead["id"])
    assert thread(client, lead_review)["status"] == "done"
    assert lead["decision"] is None
    levels = {(st["workflow_id"], st["options"]["org"]["role"]): st["options"]["org"]["level"] for st in client.started}
    assert levels[("lead-intake", "cto")] == "top" and levels[("lead-review", "cto")] == "top"
    assert levels[("lead-intake", "lead")] == "team" and levels[("lead-review", "lead")] == "team"
    answer(client, tid, "accept")
    v = wait_status(client, m["id"], "done")
    assert v["mission"]["children"][0]["status"] == "done" and v["mission"]["children"][0]["children"][0]["status"] == "done"


# ------------------------------------------------------------------ the ready-made orgs (Marketplace › Orgs)

REPO = Path(__file__).resolve().parents[4]
PLUGIN = Path(__file__).resolve().parents[2]


def _ids(folder: Path, key: str) -> set[str]:
    out = set()
    for f in [*folder.glob("*.yaml"), *folder.glob("*.md")]:
        text = f.read_text()
        head = text.split("---")[1] if f.suffix == ".md" else text
        out.add(str((yaml.safe_load(head) or {}).get(key) or f.stem))
    return out


def test_every_template_is_a_valid_org_that_names_only_what_keel_has():
    workflows = _ids(REPO / "content" / "workflows", "id") | _ids(PLUGIN / "content" / "workflows", "id")
    agents = _ids(REPO / "content" / "agents", "name") | _ids(PLUGIN / "content" / "agents", "name")
    ts = templates.all_templates()
    assert [t["id"] for t in ts] == [tid for tid, _ in templates.ORDER] and len(ts) >= 6
    for t in ts:
        org = config.parse(t["yaml"])
        assert org.projects == [], f"{t['id']}: a template works on any project"
        people = config.humans(org)
        assert people, f"{t['id']}: someone gets the reports"
        for rid, r in org.roles.items():
            assert set(r.workflows) <= workflows, f"{t['id']}.{rid}: {set(r.workflows) - workflows}"
            assert r.human or r.agent in agents, f"{t['id']}.{rid}: no agent {r.agent}"
            # a role that plans for a team reviews it too, and a role with a team plans with the org's flows
            if not r.human and config.team(org, rid):
                assert r.plan == "lead-intake" and r.review == "lead-review", f"{t['id']}.{rid}"
        # every agent can be reached from a person: no role sits alone
        seen, todo = set(people), list(people)
        while todo:
            for x in config.team(org, todo.pop()):
                if x not in seen:
                    seen.add(x)
                    todo.append(x)
        assert seen == set(org.roles), f"{t['id']}: nobody gives work to {set(org.roles) - seen}"


def test_the_template_routes_and_a_template_saves_as_your_org(client):
    ts = client.get("/org/templates").json()
    assert [t["id"] for t in ts] == ["solo", "small-team", "bug-squad", "startup", "maintainer", "quality"]
    solo = ts[0]
    assert (solo["people"], solo["agents"], solo["levels"]) == (1, 2, 2) and solo["title"] == "Solo developer"
    st = client.get("/org/templates/startup").json()
    assert (st["people"], st["agents"], st["levels"]) == (1, 6, 4)
    assert {"from": ["web-lead"], "to": ["api-lead"], "can": ["ask", "notify"]} in st["links"]
    assert next(r for r in st["roles"] if r["id"] == "cto")["team"] == ["web-lead", "api-lead"]
    r = client.get("/org/templates/nope")
    assert r.status_code == 404 and "No org template" in r.json()["error"]
    saved = put(client, "my-startup", st["yaml"])
    assert saved["problems"] == [] and saved["name"] == "Startup"


def test_a_solo_mission_goes_straight_to_the_one_who_does_it(client):
    put(client, "solo", client.get("/org/templates/solo").json()["yaml"].replace("[small, feature, fix, quick]", "[small]"))
    m = assign(client, "solo", role="dev")
    v = wait_status(client, m["id"], "reported")
    assert v["mission"]["children"] == [] and v["mission"]["report"]["summary"].startswith("The small flow ended")
    assert client.started[0]["options"]["org"]["level"] == "top" and "where" not in client.started[0]
    client.post(f"/org/missions/{m['id']}/accept", json={"why": "ok"})
    wait_status(client, m["id"], "done")


def test_stop_stops_the_tree(client):
    put(client, text=PROPOSE)
    m = assign(client)
    tid = wait_for(lambda: mission(client, m["id"])["mission"]["thread_id"], what="the plan flow")
    wait_for(lambda: thread(client, tid).get("waiting"), what="the plan gate")
    v = client.post(f"/org/missions/{m['id']}/stop").json()
    assert v["status"] == "stopped"
    assert wait_thread(client, tid)["status"] in ("stopped", "done")
    assert client.delete("/org/orgs/shop").json() == {"ok": True}


def test_an_org_with_open_missions_cannot_be_deleted(client):
    put(client, text=PROPOSE)
    assign(client)
    r = client.delete("/org/orgs/shop")
    assert r.status_code == 409


# ------------------------------------------------------------------ the actions' own checks

def _inp(data, text, tid="t1"):
    return ActionInput(root=".", phase="review", title="t", ac=None, acs=[], fake=True, flow="lead-intake",
                       data={**data, "plan_text": text}, thread_id=tid, project="demo")


def _plan(*children):
    import json
    return "plan\n```keel-org\n" + json.dumps({"summary": "s", "children": list(children)}) + "\n```"


@pytest.fixture
def lead_mission(env):
    store.save_org("shop", "Shop", config.EXAMPLE)
    m = store.add(org_id="shop", role="lead", project_id="demo", title="t", workflow="lead-intake", stage="plan", status="running")
    store.update(m["id"], run_key="k1")
    return m


def test_check_refuses_a_bad_plan_before_you_see_it(lead_mission):
    org = {"org": {"mission": lead_mission["id"], "key": "k1"}}
    bad = actions.check(_inp(org, _plan({"role": "me", "title": "a", "request": "b"})))
    assert not bad.ok and bad.final and "may not give work to me" in bad.note
    ok = actions.check(_inp(org, _plan({"role": "fe", "title": "badge", "request": "add the badge"})))
    assert ok.ok and "1. Frontend engineer (fe) · small: badge" in ok.update["data"]["plan_gate_detail"]
    assert store.children(lead_mission["id"]) == []          # checking gives nothing out


def test_delegate_refuses_a_flow_without_the_mission_key(lead_mission):
    r = actions.delegate(_inp({"org": {"mission": lead_mission["id"], "key": "wrong"}},
                              _plan({"role": "fe", "title": "a", "request": "b"})))
    assert not r.ok and "key does not match" in r.note
    r = actions.delegate(_inp({}, _plan()))
    assert not r.ok and "does not run an org mission" in r.note
    assert store.children(lead_mission["id"]) == []


def test_delegate_keeps_to_the_links_workflows_and_limits(lead_mission):
    org = {"org": {"mission": lead_mission["id"], "key": "k1"}}
    r = actions.delegate(_inp(org, _plan({"role": "me", "title": "a", "request": "b"})))
    assert not r.ok and "may not give work to me" in r.note
    r = actions.delegate(_inp(org, _plan({"role": "fe", "title": "a", "request": "b", "workflow": "deploy"})))
    assert not r.ok and "fe may not run 'deploy'" in r.note
    r = actions.delegate(_inp(org, _plan(*[{"role": "fe", "title": f"a{i}", "request": "b"} for i in range(9)])))
    assert not r.ok and "more than this org allows" in r.note
    r = actions.delegate(_inp(org, "a plan with no block"))
    assert not r.ok and r.final and "keel-org block" in r.note
    assert store.children(lead_mission["id"]) == []
    ok = actions.delegate(_inp(org, _plan({"role": "be", "title": "api", "request": "add count"})))
    assert ok.ok and [c["role"] for c in store.children(lead_mission["id"])] == ["be"]
    again = actions.delegate(_inp(org, _plan({"role": "be", "title": "api", "request": "add count"})))
    assert again.ok and "Already gave out 1" in again.note and len(store.children(lead_mission["id"])) == 1


def test_a_deep_mission_cannot_give_work_further_down(lead_mission):
    store.save_org("shop", "Shop", config.EXAMPLE.replace("depth: 4", "depth: 1") if "depth:" in config.EXAMPLE
                   else config.EXAMPLE.replace("send_backs: 3 }", "send_backs: 3, depth: 1 }"))
    r = actions.delegate(_inp({"org": {"mission": lead_mission["id"], "key": "k1"}},
                              _plan({"role": "fe", "title": "a", "request": "b"})))
    assert not r.ok and "levels deep at most" in r.note


def test_the_block_reader_takes_the_last_valid_json_object():
    text = "```keel-org\n{not json}\n```\nlater\n```keel-org\n{\"children\": []}\n```"
    assert actions.block(text, "keel-org") == {"children": []}
    assert actions.block("```keel-org\n[1]\n```", "keel-org") is None
    assert actions.block(None, "keel-org") is None
