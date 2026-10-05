"""The hunt and hunt-next workflows on the fake model: every gate exit, ingest (lanes, severity, duplicates), verdicts
(recipe, two runs, the 5xx floor), the report's refusal, hunt-next's refusals and routing, and closing with a note."""

import json
import subprocess

import pytest
from conftest import decide, start, wait
from keel_engine.runtime import hunt
from keel_engine.workflows.templates import get_template
from keel_engine.workflows.validate import validate
from test_flows_base import answers, values

SETTINGS = {"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause"}
API = "src/scores/__init__.py"


def fenced(items) -> str:
    return "```json\n" + json.dumps(items) + "\n```"


def cand(title, where, symptom, **kw):
    return {"title": title, "where": where, "symptom": symptom, "claim": "CLAIM-THEORY " + title, **kw}


HUNTERS = {
    "security:api": fenced([cand("Unauthenticated list leaks rows", [f"{API}:10"], "GET /scores without a token returns every row",
                                 severity="critical"),
                            cand("Delete returns 500", [f"{API}:40"], "DELETE /scores/1 twice returns 500")]) + "\nFINDINGS: 2",
    # cites a backend file from the web lane: the whole batch is refused
    "security:web": fenced([cand("Token in local storage", ["web/src/App.tsx:3", f"{API}:12"], "the token is readable by any script")])
                    + "\nFINDINGS: 1",
    # the same place as security:api's first one (4 lines away): merged as corroboration
    "technical:api": fenced([cand("List has no auth check", [f"{API}:14"], "the list endpoint answers without a login"),
                             cand("No sort order", [f"{API}:80"], "the list comes back in a different order each time",
                                  kind="unspecified")]) + "\nFINDINGS: 2",
    "technical:web": fenced([]) + "\nFINDINGS: 0",
    # says it found two, gives no JSON: refused
    "contract-drift:both": "I found two drifts in the contract.\nFINDINGS: 2",
}


def proof(cid, verdict, severity=None, runs=2, body=None, evidence="ran it twice"):
    v = {"id": cid, "verdict": verdict, "evidence": evidence}
    if verdict == "proven":
        v.update(severity=severity, runs=runs, recipe={"file": f"{cid}.sh", "body": body or f"curl -s localhost:8080/scores  # {cid}"})
    return fenced([v]) + f"\nPROOF: {verdict}"


def start_hunt(client, repo, monkeypatch, mode="semi", provers=None, groups=None, lenses=("security", "technical", "contract-drift")):
    rounds: dict[str, int] = {}
    # the demo is api only; a web file gives the sweep its web lane (a lane with no files gets no hunter)
    (repo / "web" / "src").mkdir(parents=True, exist_ok=True)
    (repo / "web" / "src" / "App.tsx").write_text("export const App = () => null;\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "chore: web"], cwd=repo, check=True)

    def prover(req):
        cid = req.item["id"]
        rounds[cid] = rounds.get(cid, 0) + 1
        return (provers or {})[cid](rounds[cid])

    seen = answers(monkeypatch, {
        "hunter": lambda r: HUNTERS[r.item["id"]],
        "prover": prover,
        "investigator": lambda r: fenced(groups if groups is not None else []),
    })
    tid = start(client, repo, workflow="hunt", title="Bug hunt",
                settings={**SETTINGS, "hunt_mode": mode, "hunt_scope": "all", "hunt_lenses": list(lenses)})
    return tid, seen, rounds


def git_log(repo) -> str:
    return subprocess.run(["git", "log", "--format=%s"], cwd=repo, capture_output=True, text=True).stdout


def test_the_templates_validate():
    for name in ("hunt", "hunt-next", "init"):
        assert validate(get_template(name)) == [], name


def test_a_semi_hunt_from_lenses_to_triage(client, repo, monkeypatch):
    provers = {
        # round 1 files a 500 as moderate: refused (a 5xx is never below high); round 2 says high
        "F-001": lambda n: proof("F-001", "proven", "moderate" if n == 1 else "high", evidence="GET /scores returns 500"),
        "F-002": lambda n: proof("F-002", "false", evidence="the second DELETE returns 404, see test_scores.py:20"),
        "F-003": lambda n: proof("F-003", "proven", "low"),
    }
    tid, seen, rounds = start_hunt(client, repo, monkeypatch, provers=provers,
                                   groups=[{"members": ["F-001", "F-003"], "lead": "F-001", "cause": "the list query has no owner filter"}])
    s = wait(client, tid)
    assert s["waiting"]["step"] == "confirm_lenses" and s["waiting"]["choices"] == ["confirm", "stop"]
    assert "security [api, web]" in s["waiting"]["detail"] and "5 hunter(s)" in s["waiting"]["detail"]

    s = decide(client, tid, "approve", payload={"choice": "confirm"})
    # semi: the flow stops after the sweep with the candidates page
    assert s["waiting"]["step"] == "sweep_gate", s
    hunters = sorted(r.item["id"] for r in seen if r.agent == "hunter")
    assert hunters == ["contract-drift:both", "security:api", "security:web", "technical:api", "technical:web"]
    sec = next(r for r in seen if r.agent == "hunter" and r.item["id"] == "security:api")
    assert "## " not in sec.item["brief"] and "what an actor can reach" in sec.item["brief"].lower()
    assert sec.item["dependency_scan"]                  # the security lens ran the dependency audit first

    v = values(client, tid)
    run_id = v["data"]["hunt"]["run"]
    cands = {c["id"]: c for c in hunt.candidates("demo", run_id)}
    assert sorted(cands) == ["F-001", "F-002", "F-003"]
    assert cands["F-001"]["severity"] is None and cands["F-001"]["also"] == ["technical:api"]
    assert cands["F-003"]["kind"] == "unspecified"
    swept = hunt.get_run("demo", run_id)["swept"]
    assert "outside the web lane" in swept["security:web"]["refused"]
    assert "no JSON list" in swept["contract-drift:both"]["refused"]
    page = (repo / "docs" / "hunts" / run_id / "candidates.md").read_text()
    assert "UNVERIFIED" in page and "F-001" in page
    assert "candidates.md" in s["waiting"]["detail"]

    s = decide(client, tid, "approve", payload={"choice": "prove"})
    assert s["waiting"]["step"] == "verdicts_gate", s
    assert rounds == {"F-001": 2, "F-002": 1, "F-003": 1}
    provers_seen = [r for r in seen if r.agent == "prover"]
    assert all("CLAIM-THEORY" not in r.prompt for r in provers_seen), "a prover gets the symptom, never the claim"
    assert "cannot be moderate" in provers_seen[-1].prompt
    cands = {c["id"]: c for c in hunt.candidates("demo", run_id)}
    assert (cands["F-001"]["status"], cands["F-001"]["severity"]) == ("proven", "high")
    assert cands["F-002"]["status"] == "false" and cands["F-003"]["severity"] == "low"

    s = decide(client, tid, "approve", payload={"choice": "report"})
    assert s["waiting"]["step"] == "triage" and s["waiting"]["choices"] == ["take", "close", "stop"], s
    groups = hunt.groups("demo", run_id)
    assert [(g["id"], g["lead"]) for g in groups] == [("G-01", "F-001")]
    report = (repo / "docs" / "hunts" / run_id / "report.md").read_text()
    assert "## Severity rubric" in report and "never below **high**" in report and "### G-01 · high" in report
    assert (repo / "docs" / "hunts" / run_id / "repro" / "F-001.sh").is_file()
    assert git_log(repo).splitlines()[0] == f"docs(HUNT-{run_id}): bug hunt: 2 proven, 0 suspected"
    assert "G-01 · high · defect" in s["waiting"]["detail"]

    # close one without a note: refused, back to the triage; with a note: closed, back to the triage
    s = decide(client, tid, "approve", payload={"choice": "close", "id": "F-002", "as": "wontfix"})
    assert s["waiting"]["step"] == "triage"
    assert not hunt.candidates("demo", run_id)[1]["close"]
    s = decide(client, tid, "approve", why="not a bug, the prover showed it", payload={"choice": "close", "id": "F-002", "as": "wontfix"})
    assert s["waiting"]["step"] == "triage" and "Closed F-002 as wontfix" in s["waiting"]["detail"]
    assert hunt.candidates("demo", run_id)[1]["close"]["note"] == "not a bug, the prover showed it"

    s = decide(client, tid, "approve", payload={"choice": "stop"})
    assert s["status"] == "done"
    assert any(line.startswith("gate triage stop") for line in s["gate_log"])

    listed = client.get("/projects/demo/hunts").json()
    assert listed[0]["run"] == run_id and listed[0]["counts"]["proven"] == 2 and listed[0]["open"] == 2
    one = client.get(f"/projects/demo/hunts/{run_id}").json()
    assert [g["id"] for g in one["groups"]] == ["G-01"] and one["report_markdown"].startswith("# Bug hunt")
    assert next(c for c in one["candidates"] if c["id"] == "F-001")["recipe"]["file"] == "F-001.sh"
    assert client.get("/projects/demo/hunts/nope").status_code == 404


def test_an_auto_hunt_stops_only_at_the_lenses_and_the_triage_then_hands_over(client, repo, monkeypatch):
    provers = {"F-001": lambda n: proof("F-001", "proven", "critical"),
               "F-002": lambda n: proof("F-002", "unproven", evidence="could not make it fail in 5 tries"),
               "F-003": lambda n: proof("F-003", "proven", "moderate")}
    tid, seen, _ = start_hunt(client, repo, monkeypatch, mode="auto", provers=provers, groups=[])
    s = wait(client, tid)
    assert s["waiting"]["step"] == "confirm_lenses"
    # the user drops contract-drift in their note
    s = decide(client, tid, "approve", why="drop contract-drift")
    assert s["waiting"]["step"] == "triage", s
    assert not any(r.item["id"] == "contract-drift:both" for r in seen if r.agent == "hunter")
    assert any("gate sweep_gate approve: not asked" in line for line in s["gate_log"])
    run_id = values(client, tid)["data"]["hunt"]["run"]
    report = (repo / "docs" / "hunts" / run_id / "report.md").read_text()
    assert "approved without a person" in report and "## Suspected" in report
    # only one proven finding was grouped by nobody: the investigator never ran for a single proven pair? (two proven)
    assert [r.agent for r in seen].count("investigator") == 1

    # take: hunt-next starts, takes the critical F-001 (a defect) and starts a fix flow with the recipe
    s = decide(client, tid, "approve", payload={"choice": "take"})
    assert s["status"] == "done"
    nxt = s["children"][0]
    assert nxt["workflow"] == "hunt-next"
    n = wait(client, nxt["thread_id"])
    # hunt-next ends at the hand-off (a real run once asked "close it: fixed?" while the fix had only just started)
    assert n["status"] == "done", n
    fix = n["children"][0]
    assert fix["workflow"] == "fix" and fix["title"].startswith("F-001")
    eng = client.app.state.engine
    body = json.loads(client.portal.call(eng._row, fix["thread_id"])["body"])
    assert "repro/F-001.sh" in body["request"] and "curl" in body["request"]
    assert "CLAIM-THEORY" not in body["request"]
    assert "regression end-to-end test is required" in body["request"]
    fv = values(client, fix["thread_id"])
    assert fv["data"]["needs_e2e"] is True and fv["data"]["symptoms"][0].startswith("F-001:")
    assert hunt.candidates("demo", run_id)[0]["dispatch"]["flow"] == "fix"
    assert not hunt.candidates("demo", run_id)[0].get("close")


def test_the_child_flows_end_closes_or_reopens_its_group(repo):
    run = _proven_run(repo)
    r = hunt.take("demo", str(repo), run)
    data = {"seed": r["seed"]}
    assert hunt.candidates("demo", run)[0]["dispatch"]
    # failed / stopped: open again, so the next hunt-next takes it
    assert "open again" in hunt.child_finished("demo", str(repo), data, "stopped", "F-001 fix", "fix")
    assert not hunt.candidates("demo", run)[0].get("dispatch")
    # done: closed as fixed, the branch and the commit in the note
    msg = hunt.child_finished("demo", str(repo), data, "done", "F-001 fix", "fix")
    closed = hunt.candidates("demo", run)[0]["close"]
    assert msg.startswith("Closed") and closed["as"] == "fixed" and "keel does not merge" in closed["note"] and closed["sha"]
    # a flow no hunt started: nothing
    assert hunt.child_finished("demo", str(repo), {}, "done", "x", "fix") is None


def test_an_unspecified_finding_goes_to_the_feature_flow_and_nothing_open_ends(client, repo, monkeypatch):
    run = _proven_run(repo, kind="unspecified")
    answers(monkeypatch, {})
    tid = start(client, repo, workflow="hunt-next", title="next", settings=SETTINGS)
    s = wait(client, tid)
    assert s["status"] == "done" and s["children"][0]["workflow"] == "feature"
    # the group is dispatched: nothing is open, so the next hunt-next ends at once
    s = wait(client, start(client, repo, workflow="hunt-next", title="next", settings={**SETTINGS, "hunt_run": run}))
    assert s["status"] == "done" and not s.get("children")


def _proven_run(repo, kind="defect", commit=True, waiting=False) -> str:
    """A run with one proven finding and a rendered report, built through the backlog functions."""
    r = hunt.new_run("demo", str(repo), thread_id="t", mode="auto", fast=False, scope={"mode": "all", "paths": []},
                     configured=["security"], proposed=["security"])
    hunt.update_run("demo", r["run"], lenses={**r["lenses"], "confirmed": ["security"]})
    items = [cand("Leak", [f"{API}:10"], "GET /scores leaks rows", kind=kind)]
    if waiting:
        items.append(cand("Other", [f"{API}:90"], "something else"))
    out = hunt.ingest("demo", str(repo), r["run"], "security:api", items)
    assert out["added"]
    assert hunt.record_verdict("demo", str(repo), r["run"], "F-001", {"verdict": "proven", "severity": "high", "runs": 2,
                                                                       "evidence": "rows of two owners",
                                                                       "recipe": {"file": "F-001.http", "body": "GET /scores"}}) is None
    if not waiting:
        rel, _ = hunt.write_report("demo", str(repo), r["run"])
        assert rel
        if commit:
            subprocess.run(["git", "add", "docs/hunts"], cwd=repo, check=True)
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "report"], cwd=repo, check=True)
    return r["run"]


@pytest.mark.parametrize("case", ["unverified", "uncommitted"])
def test_hunt_next_refuses_until_every_verdict_is_in_and_the_report_is_committed(client, repo, monkeypatch, case):
    _proven_run(repo, commit=case != "uncommitted", waiting=case == "unverified")
    answers(monkeypatch, {})
    s = wait(client, start(client, repo, workflow="hunt-next", title="next", settings={**SETTINGS, "fix_attempts": 0}))
    assert s["waiting"]["kind"] == "fix" and s["waiting"]["step"] == "take"
    want = "have no verdict yet" if case == "unverified" else "is not committed"
    assert want in s["waiting"]["detail"]
    assert not s.get("children")


# ------------------------------------------------------------------ the backlog itself

def test_ingest_enforces_the_lane_the_cap_and_merges_duplicates(repo):
    r = hunt.new_run("p", str(repo), thread_id="t", mode="semi", fast=True, scope={"mode": "diff", "paths": []},
                     configured=["security", "technical"], proposed=["security", "technical"])
    run = r["run"]
    out = hunt.ingest("p", str(repo), run, "security:api", [cand("x", [f"{API}:1"], "y")])
    assert "not one of the confirmed lenses" in out["refused"]
    hunt.update_run("p", run, lenses={**r["lenses"], "confirmed": ["security", "technical"]})
    # fast: the cap of 12 is halved
    many = [cand(f"t{n}", [f"{API}:{n * 100}"], "s") for n in range(7)]
    assert "over the cap of 6" in hunt.ingest("p", str(repo), run, "security:api", many)["refused"]
    assert "outside the api lane" in hunt.ingest("p", str(repo), run, "security:api", [cand("w", ["web/src/A.tsx:2"], "s")])["refused"]
    assert "symptom is required" in hunt.ingest("p", str(repo), run, "security:api", [{"title": "t", "where": ["a:1"]}])["refused"]
    out = hunt.ingest("p", str(repo), run, "security:api", [cand("a", [f"{API}:10"], "s", severity="high"), cand("b", ["README.md"], "s")])
    assert out["added"] == ["F-001", "F-002"] and out["dropped_severity"] == 1
    out = hunt.ingest("p", str(repo), run, "technical:api", [cand("c", [f"{API}:20"], "s"), cand("d", [f"{API}:5"], "s")])
    assert out["added"] == [] and out["merged"] == ["F-001", "F-001"]
    assert hunt.candidates("p", run)[0]["also"] == ["technical:api"]
    assert hunt.candidates("p", run)[0]["severity"] is None


def test_verdicts_need_a_recipe_run_twice_and_respect_the_5xx_floor(repo):
    run = hunt.new_run("p", str(repo), thread_id="t", mode="auto", fast=False, scope={"mode": "all", "paths": []},
                       configured=["technical"], proposed=["technical"])["run"]
    hunt.update_run("p", run, lenses={"confirmed": ["technical"]})
    hunt.ingest("p", str(repo), run, "technical:api", [cand("a", [f"{API}:1"], "s"), cand("b", [f"{API}:100"], "s")])
    root = str(repo)
    assert "needs a recipe" in hunt.record_verdict("p", root, run, "F-001", {"verdict": "proven", "severity": "high", "runs": 2, "evidence": "e"})
    assert "twice" in hunt.record_verdict("p", root, run, "F-001", {"verdict": "proven", "severity": "high", "runs": 1, "evidence": "e",
                                                                     "recipe": "curl x"})
    assert "never a test file" in hunt.record_verdict("p", root, run, "F-001", {"verdict": "proven", "severity": "high", "runs": 2,
                                                                                "evidence": "e", "recipe": {"file": "a.spec.ts", "body": "x"}})
    assert "cannot be low" in hunt.record_verdict("p", root, run, "F-001", {"verdict": "proven", "severity": "low", "runs": 2,
                                                                            "evidence": "HTTP 503", "recipe": "curl x"})
    assert "evidence" in hunt.record_verdict("p", root, run, "F-002", {"verdict": "false"})
    # the last round: the floor is applied instead of refused, a proof without a recipe becomes unproven
    assert hunt.record_verdict("p", root, run, "F-001", {"verdict": "proven", "severity": "low", "runs": 2, "evidence": "HTTP 503",
                                                         "recipe": "curl x"}, force=True) is None
    assert hunt.record_verdict("p", root, run, "F-002", {"verdict": "proven", "severity": "low", "evidence": "x"}, force=True) is None
    c = {x["id"]: x for x in hunt.candidates("p", run)}
    assert c["F-001"]["severity"] == "high" and "raised from low to high" in c["F-001"]["evidence"]
    assert c["F-002"]["status"] == "unproven" and c["F-002"]["severity"] is None


def test_the_report_refuses_while_a_candidate_has_no_verdict_and_close_needs_a_note(repo):
    run = hunt.new_run("p", str(repo), thread_id="t", mode="semi", fast=False, scope={"mode": "all", "paths": []},
                       configured=["technical"], proposed=["technical"])["run"]
    hunt.update_run("p", run, lenses={"confirmed": ["technical"]})
    hunt.ingest("p", str(repo), run, "technical:api", [cand("a", [f"{API}:1"], "s"), cand("b", [f"{API}:100"], "s")])
    rel, why = hunt.write_report("p", str(repo), run)
    assert rel is None and "F-001, F-002" in why and not (repo / "docs" / "hunts" / run / "report.md").exists()
    for cid in ("F-001", "F-002"):
        hunt.record_verdict("p", str(repo), run, cid, {"verdict": "proven", "severity": "moderate", "runs": 2, "evidence": "e",
                                                       "recipe": "curl x"})
    gid, why = hunt.add_group("p", run, ["F-001"], "one")
    assert gid is None and "two or more" in why
    gid, _ = hunt.add_group("p", run, ["F-001", "F-002"], "the same missing check", "F-002")
    assert gid == "G-01" and hunt.add_group("p", run, ["F-001", "F-002"], "again")[1].endswith("already belongs to G-01")
    rel, _ = hunt.write_report("p", str(repo), run)
    first = (repo / rel).read_text()
    hunt.write_report("p", str(repo), run)
    assert (repo / rel).read_text() == first, "a second render of the same backlog is byte-identical"
    assert hunt.close("p", run, "G-01", "fixed", "")[0] is False
    assert hunt.close("p", run, "G-01", "done", "x")[0] is False
    ok, msg = hunt.close("p", run, "G-01", "accepted", "known, low traffic")
    assert ok and "2 finding(s)" in msg
    assert hunt.close("p", run, "G-01", "fixed", "x")[1].endswith("already closed as accepted.")


def test_the_engine_close_endpoint(client, repo):
    run = _proven_run(repo)
    r = client.post(f"/projects/demo/hunts/{run}/close", json={"id": "F-001", "as": "wontfix", "note": ""})
    assert r.status_code == 400 and "note" in r.json()["error"]
    r = client.post(f"/projects/demo/hunts/{run}/close", json={"id": "F-001", "as": "wontfix", "note": "by design"})
    assert r.status_code == 200 and r.json()["candidates"][0]["close"]["as"] == "wontfix"


def test_a_lane_without_files_gets_no_hunter(tmp_path):
    # Real run (lab, node project with no web code): the web hunters searched until they ran out of turns.
    import subprocess
    root = tmp_path / "p"
    (root / "src").mkdir(parents=True)
    (root / "src" / "cart.js").write_text("export const a = 1;\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    from keel_engine.runtime import hunt
    assert hunt.lanes_present(str(root)) == {"api"}
    pairs = hunt.sweep_pairs(hunt.settings(str(root)), ["behavioral", "security"])
    assert pairs and all(p.endswith(":api") or p.endswith(":both") for p in pairs)


def test_flow_options_from_the_api_reach_the_hunt():
    # Real run: the API sends options as data ({"hunt_mode": "auto"}); the hunt read only settings and ran semi.
    from types import SimpleNamespace
    from keel_engine.runtime import hunt_actions
    a = SimpleNamespace(settings={}, data={"hunt_mode": "auto", "lenses": "security", "seed": {"scope": "all"}})
    assert hunt_actions._options(a) == {"mode": "auto", "scope": "all", "lenses": "security", "fast": None, "run": None}
