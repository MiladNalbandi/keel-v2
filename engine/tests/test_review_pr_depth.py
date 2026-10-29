"""keel 0.18.57 review depth (content/workflows/review-pr.yaml, runtime/ticket_actions.py), with the fake model: a large
change is chunked by area with one bug reviewer each and the lenses its paths need; every job item reaches its agent
whole; claims merges and dedupes findings, compares what each reviewer read with what it was given (a partial review
names the unread files, one that read nothing stops), warns when the tokens per changed line say the diff was not read,
and sends undecided items to a second look that reads the code (met / not-met, or explicitly unverified)."""

import json

from conftest import start, wait
from test_flows_base import answers
from test_v02 import commit_all, git, write
from keel_engine.runtime import prompts, stack_setup
from keel_engine.runtime import ticket_actions as ta
from keel_engine.runtime.actions import ActionInput

TICKET = """Dispatch and tracking

## Acceptance criteria
- [ ] Old shipments are not handed over again
- [ ] A carrier event is stored once
"""


def f(path: str, added: int = 10, removed: int = 0) -> dict:
    return {"file": path, "added": added, "removed": removed, "bucket": "other", "rank": 4}


# ------------------------------------------------------------------ areas, lenses, chunks

def test_areas_and_lenses_follow_the_changed_paths():
    assert ta.area_of("backend/migrations/Version20261026100000.php") == "migrations"
    assert ta.area_of("db/migrate/20261026_add_status.rb") == "migrations"
    assert ta.area_of("backend/.env") == "deploy-config"
    assert ta.area_of("backend/config/services.yaml") == "deploy-config"
    assert ta.area_of("backend/compose.yaml") == "deploy-config"
    assert ta.area_of("backend/deploy/crontab") == "deploy-config"
    assert ta.area_of("backend/src/Shipping/Infrastructure/Messaging/Handler/CarrierTrackingUpdateHandler.php") == "message-pipeline"
    assert ta.area_of("backend/src/Shipping/Infrastructure/Outbox/OutboxPublisher.php") == "message-pipeline"
    assert ta.area_of("backend/src/Shipping/Infrastructure/Doctrine/DoctrineShipmentStatusRepository.php") == "persistence"
    assert ta.area_of("backend/src/Shipping/Infrastructure/Controller/TrackingController.php") == "api"
    assert ta.area_of("backend/tests/Integration/Shipping/OutboxPublisherTest.php") == "tests"
    assert ta.area_of("frontend/src/tabs/TrackingTab.tsx") == "web"
    assert ta.area_of("docs/adr/0006-outbox.md") == "docs"
    assert ta.area_of("backend/src/Shipping/Domain/ShipmentStatus.php") == "core"

    assert ta.lenses_for(["backend/migrations/V2.php"]) == ["data-migration", "idempotency-retry"]
    assert ta.lenses_for(["backend/.env"]) == ["config-secrets"]
    assert set(ta.lenses_for(["src/Messaging/Handler/DispatchHandler.php"])) == {"idempotency-retry", "silent-failure"}
    assert ta.lenses_for(["frontend/src/App.tsx", "README.md"]) == []
    assert ta.lenses_for(["tests/test_retry_status.py"]) == ["test-quality"]   # a test is judged as a test, not by its name
    assert ta.lenses_for(["src/api/OrderController.kt"]) == ["silent-failure", "contract-types"]


def test_a_large_change_gets_one_reviewer_per_area_and_every_file_exactly_once():
    ranked = ([f(f"backend/migrations/V{n}.php", 60) for n in range(3)]
              + [f("backend/.env", 1, 1)]                                  # a one-line .env change still gets a reader
              + [f(f"backend/src/Messaging/Handler/H{n}.php", 300) for n in range(4)]
              + [f(f"backend/src/Doctrine/R{n}Repository.php", 90) for n in range(3)]
              + [f(f"backend/tests/T{n}Test.php", 120) for n in range(3)]
              + [f("docs/notes.md", 40)])
    chunks = ta.chunk_by_area(ranked)
    files = [x["file"] for c in chunks for x in c["files"]]
    assert sorted(files) == sorted(x["file"] for x in ranked if not x["file"].startswith("docs/"))
    assert len(files) == len(set(files))
    by_area = {" + ".join(c["areas"]): c for c in chunks}
    assert by_area["deploy-config"]["lenses"] == ["config-secrets"]
    assert by_area["migrations"]["lenses"][0] == "data-migration"
    pipeline = [c for c in chunks if c["areas"] == ["message-pipeline"]]
    assert len(pipeline) == 2 and all(c["lines"] <= ta.CHUNK_LINES for c in pipeline)   # 1,200 lines: split in two
    assert {"idempotency-retry", "silent-failure"} <= set(pipeline[0]["lenses"])

    many = [f(f"src/area{n}/Thing{n}.py", 50) for n in range(30)] + [f(f"migrations/m{n}.sql", 800) for n in range(12)]
    assert len(ta.chunk_by_area(many)) == ta.MAX_CHUNKS                    # the smallest chunks are merged


def test_each_lens_is_one_file_whose_checklist_reaches_the_reviewer():
    for name in ta.LENS_NAMES:
        text = ta.lens_text(name)
        assert text == " ".join(ta.LENS_FALLBACK[name].split()), name     # the file holds the measured wording
        assert len(text) < 700, name                                       # short enough for a job item


def test_every_job_item_reaches_its_agent_whole():
    ranked = [f(f"backend/src/Shipping/Infrastructure/Messaging/Handler/VeryLongHandlerNameNumber{n}.php", 80)
              for n in range(70)] + [f("backend/.env", 2)]
    items = [{"id": f"T{n}", "from": "ticket", "text": f"criterion {n} " + "with a long explanation " * 6,
              "files": [r["file"] for r in ranked[n:n + 6]]} for n in range(1, 11)]
    items += [{"id": f"D{n}", "from": "team", "text": f"team rule {n}", "files": [ranked[0]["file"]]} for n in range(1, 11)]
    jobs = ta.plan(ranked, items)
    kinds = [j["job"] for j in jobs]
    assert kinds.count("ticket") == 3 and kinds.count("team") == 3     # four items per checklist reviewer
    review = {"files": ranked, "items": items, "source": {"base_ref": "main", "ref": "feat/x"}, "standards": []}
    for n, job in enumerate(jobs, 1):
        item = ta._job_item(n, job, review, "git diff main...feat/x -- <file>")
        assert len(json.dumps(item, indent=2, ensure_ascii=False)) <= ta.ITEM_BUDGET, job["job"]
        assert "READ line" in item["answer"]
        if job.get("lenses"):
            assert item.get("lenses")


def test_an_item_that_is_still_too_long_is_cut_with_a_marker_the_agent_must_report():
    item = {"id": "bugs-1", "job": "bugs", "files": ["x" * 80] * 200}
    text = prompts.task_prompt(agent="pr-reviewer", phase="review", step_name="one reviewer per job", title="t",
                               root="/tmp/r", ac=None, acs=[], feedback=None, item=item)
    assert "[CUT: this item is" in text and "name what was cut off" in text


# ------------------------------------------------------------------ merge, dedupe, coverage

def test_one_not_met_line_wins_and_every_problem_is_kept():
    met = {"id": "T2", "verdict": "met", "evidence": "the transaction is right", "todo": None}
    miss = {"id": "T2", "verdict": "not-met", "evidence": "one bad message stops the batch", "todo": "catch per message"}
    miss2 = {"id": "T2", "verdict": "not-met", "evidence": "nothing marks a message dead", "todo": "call markDead"}
    got = ta._merge_item(ta._merge_item(ta._merge_item(None, miss), met), miss2)
    assert got["verdict"] == "not-met"                                   # keel 0.18.56: the "met" line won, T2 was DONE
    assert "stops the batch" in got["evidence"] and "marks a message dead" in got["evidence"]
    assert ta._merge_item(ta._merge_item(None, {**met, "verdict": "unclear"}), met)["verdict"] == "met"


def test_the_merge_step_dedupes_findings_across_reviewers():
    a = {"severity": "minor", "file": "m/V2.php", "line": 49, "text": "plain index on carrier_id carrier_event_id is not unique"}
    b = {"severity": "blocking", "file": "m/V2.php", "line": 51, "text": "the carrier_event_id index is not unique: duplicates are stored"}
    c = {"severity": "blocking", "file": "src/Repo.php", "line": 74, "text": "status filter concatenated into SQL"}
    got = ta.dedupe([a, c, b])
    assert len(got) == 2
    twin = next(x for x in got if x["file"] == "m/V2.php")
    assert twin["severity"] == "blocking" and twin["seen"] == 2
    p = ta.parse_lines("READ | a.php\nREAD | `b.php`, c.php\nGAP | d.php | cut off\nT1 | minor | a.php:3 | x | y\n"
                       "F | blocking | e.php:36-50 | z | w\nCHECK | unverified | the config is not in the repo")
    assert p["read"] == ["a.php", "b.php", "c.php"] and p["gaps"] == [{"file": "d.php", "why": "cut off"}]
    assert p["findings"][0]["file"] == "a.php" and p["findings"][1]["line"] == 36   # a problem on an item line is kept
    assert p["checks"] == [{"id": None, "verdict": "unverified", "why": "the config is not in the repo"}]
    batch = ta.parse_lines("CHECK | c2 | unproven | outside the repo | needs:the broker setting\n"
                           "CHECK | c3 | confirmed | V1.php:24 sets the default | sev:blocking | conf:high | evidence:m/V1.php:24")
    assert batch["checks"][0] == {"id": "c2", "verdict": "unverified", "why": "outside the repo", "needs": "the broker setting"}
    assert batch["checks"][1]["sev"] == "blocking" and batch["checks"][1]["evidence"] == "m/V1.php:24"


def test_the_low_coverage_warning():
    assert ta.coverage_warning(11_600 / 5_268, 5_268)                      # the real case: 2.2 tokens per changed line
    assert "probably not read" in ta.coverage_warning(2.2, 5_268)
    assert ta.coverage_warning(116.0, 3_227) is None                       # a review that read the diff
    assert ta.coverage_warning(2.0, 20) is None                            # too small to tell
    assert ta.coverage_warning(None, 3_000) is None
    results = [{"usage": {"tokens_in": 4000, "tokens_cached": 6000, "tokens_out": 1600}}]
    assert round(ta.tokens_per_line(results, 5_268), 1) == 2.2


def _claims_input(results, fake=False, files=("a.php", "b.php", "c.php"), lines=3000):
    review = {"items": [{"id": "T1", "from": "ticket", "text": "it works", "files": ["a.php"]}],
              "reviewable": list(files), "added": lines, "removed": 0,
              "source": {"base_ref": "main", "ref": "feat/x"}}
    return ActionInput(root="/tmp", phase="review", title="t", ac=None, acs=[], fake=fake, flow="review-pr",
                       data={"ticket_review": review, "reviewers_results": results})


def test_claims_marks_a_partial_review_and_settles_undecided_items():
    r = ta._claims(_claims_input([{"item": "bugs-1", "text": "READ | a.php\nGAP | c.php | ran out of turns\n"
                                   "F | minor | a.php:1 | x | y\nTICKET-REVIEW: done",
                                   "usage": {"tokens_in": 300_000, "tokens_cached": 0, "tokens_out": 1}}]))
    review = r.update["data"]["ticket_review"]
    assert review["partial"] is True and review["coverage"]["unread"] == ["b.php", "c.php"]
    assert review["coverage"]["gaps"] == {"c.php": "ran out of turns"}
    assert "read 1 of 3 file(s)" in r.note
    settles = r.update["data"]["ticket_settles"]
    assert [(c["job"], c["target"]) for c in settles] == [("settle", "T1")]  # nobody judged T1: read the code, not "by hand"
    assert settles[0]["files"] == ["a.php"] and "CHECK | met" in settles[0]["answer"]
    checks = r.update["data"]["ticket_checks"]                             # the minor finding is verified too
    assert len(checks) == 1 and [c["severity"] for c in checks[0]["claims"]] == ["minor"]


def test_a_review_that_read_nothing_stops_instead_of_saying_done():
    r = ta._claims(_claims_input([{"item": "bugs-1", "text": "GAP | a.php | the diff was not readable\nGAP | b.php | same\n"
                                   "GAP | c.php | same\nS | based on the file list\nTICKET-REVIEW: done"}]))
    assert r.stop and "no reviewer read any of the 3 changed file(s)" in r.note
    # no READ lines at all and far too few tokens for the diff (keel 0.18.56's prompt): stopped too
    low = [{"item": "all-1", "text": "S | looks fine from the file list\nTICKET-REVIEW: done",
            "usage": {"tokens_in": 4000, "tokens_cached": 6000, "tokens_out": 1600}}]
    r = ta._claims(_claims_input(low, lines=5268))
    assert r.stop and "the change was not read" in r.note and "tokens per changed line" in r.note
    # the same with READ lines: not stopped, but partial with the warning in the report
    low[0]["text"] = "READ | a.php\nREAD | b.php\nREAD | c.php\n" + low[0]["text"]
    r = ta._claims(_claims_input(low, lines=5268))
    review = r.update["data"]["ticket_review"]
    assert not r.stop and review["partial"] and "probably not read" in review["coverage"]["warning"]
    assert "WARNING" in r.note


def test_the_verifier_settles_items_and_marks_what_it_could_not_read():
    review = {"items": [{"id": "T1", "from": "ticket", "text": "old rows are not handed over again"},
                        {"id": "T2", "from": "ticket", "text": "an event is stored once"},
                        {"id": "T3", "from": "ticket", "text": "terminal states never change"},
                        {"id": "T4", "from": "ticket", "text": "a bad message does not stop the batch"}],
              "judged": {"T2": {"id": "T2", "verdict": "unclear", "evidence": "did not read the migration"},
                         "T4": {"id": "T4", "verdict": "not-met", "evidence": "Publisher.php:40 throws in the loop"}},
              "findings": [{"severity": "blocking", "file": "x.php", "line": 3, "text": "retries forever"},
                           {"severity": "minor", "file": "y.php", "line": 9, "text": "sort goes into the SQL"},
                           {"severity": "minor", "file": "z.php", "line": 2, "text": "time zone dropped"},
                           {"severity": "blocking", "file": "Publisher.php", "line": 40, "text": "one throw stops the loop"}],
              "claims": [{"id": "c1", "targets": [["finding", 0]]}, {"id": "c2", "targets": [["finding", 1]]},
                         {"id": "c3", "targets": [["item", "T4"], ["finding", 3]]}, {"id": "c4", "targets": [["finding", 2]]}],
              "reviewable": ["x.php"], "coverage": {"files": 1, "read": 1, "unread": [], "known": True},
              "source": {"branch": "feat/x", "base": "main"}, "title": "t",
              # keel 0.18.68 a quality note on the confirmed finding's line is the same problem twice: it goes
              "quality": [{"file": "Publisher.php", "line": 41, "rule": "AGENTS.md", "text": "a throw in a loop"},
                          {"file": "Other.php", "line": 5, "rule": "like Other2.php", "text": "naming"}]}
    checks = [{"id": "check-1", "claims": [{"id": "c1"}, {"id": "c2"}, {"id": "c3"}, {"id": "c4"}]}]
    settles = [{"id": "settle-1", "target": "T1"}, {"id": "settle-2", "target": "T2"}, {"id": "settle-3", "target": "T3"}]
    check_results = [{"item": "check-1", "text": "CHECK | c1 | unproven | the handler is generated | needs:the build output\n"
                                                 "CHECK | c2 | rejected | sort goes through an allow-list at Repo.php:77\n"
                                                 "CHECK | c3 | confirmed | Publisher.php:40 has no catch | sev:blocking"}]
    settle_results = [{"item": "settle-1", "text": "CHECK | not-met | m/V1.php:24 | the default gives old rows label_generated"},
                      {"item": "settle-2", "text": "CHECK | met | m/V2.php:54 | a UNIQUE index"},
                      {"item": "settle-3", "text": "CHECK | unverified | the status SQL lives in a stored procedure"}]
    a = ActionInput(root="/tmp", phase="review", title="t", ac=None, acs=[], fake=True, flow="review-pr",
                    data={"ticket_review": review, "ticket_checks": checks, "check_results": check_results,
                          "ticket_settles": settles, "settle_results": settle_results})
    r = ta._record(a)
    out = r.update["data"]["ticket_review"]
    items = {it["id"]: it for it in out["items"]}
    assert items["T1"]["verdict"] == "not-met" and "settled by reading the code" in items["T1"]["evidence"]
    assert items["T2"]["verdict"] == "met"
    assert items["T3"]["verdict"] == "unclear" and items["T3"]["evidence"].startswith("unverified:")
    assert items["T4"]["verdict"] == "not-met"                            # the shared claim confirmed both
    assert [f["text"] for f in out["findings"]] == ["one throw stops the loop"]   # only what was confirmed
    assert [q["text"] for q in out["quality"]] == ["naming"]
    assert [f["text"] for f in out["dropped"]] == ["sort goes into the SQL"]
    assert {f["text"]: f["why"] for f in out["not_checked"]} == {
        "retries forever": "unproven: the build output", "time zone dropped": "the second look gave no answer"}
    text = r.update["show"]
    assert "NOT CHECKED" in text and "retries forever" in text.split("NOT CHECKED", 1)[1]
    assert "retries forever" not in text.split("NOT CHECKED", 1)[0]       # never shown as a finding
    assert "no reviewer gave a verdict" not in text


# ------------------------------------------------------------------ the whole flow

def _big_branch(repo):
    git(repo, "checkout", "-q", "-b", "feat/dispatch")
    files = {
        "backend/migrations/V1.php": "<?php\n" + "// migration line\n" * 120,
        "backend/.env": "TRACKING_MAX_ATTEMPTS=8\n",
        "backend/config/services.yaml": "services:\n" + "  x: y\n" * 30,
        "backend/src/Messaging/Handler/TrackingHandler.php": "<?php\n" + "// handler line\n" * 260,
        "backend/src/Outbox/OutboxPublisher.php": "<?php\n" + "// publisher line\n" * 200,
        "backend/src/Doctrine/StatusRepository.php": "<?php\n" + "// repo line\n" * 150,
        "backend/tests/OutboxPublisherTest.php": "<?php\n" + "// test line\n" * 100,
    }
    for rel, text in files.items():
        write(repo, rel, text)
    commit_all(repo, "feat: dispatch")
    git(repo, "checkout", "-q", "main")
    return files


def test_a_large_review_is_chunked_and_a_reviewer_that_skips_files_makes_it_partial(client, repo, monkeypatch):
    files = _big_branch(repo)

    def reviewer(req):
        item = req.item
        if item["job"] in ("check", "settle"):
            return "CHECK | met | backend/migrations/V1.php:1 | read it\nTICKET-REVIEW: done"
        mine = [str(x).split(" ")[0] for x in item["files"] if not str(x).startswith("…")]
        if item.get("area") == "message-pipeline":                     # this reviewer reads one of its two files
            mine = mine[:1]
        if item["job"] != "bugs":                                      # the others read only the migration
            mine = [x for x in mine if "migrations" in x] or mine[:0]
        lines = [f"READ | {x}" for x in mine] + [f"{i['id']} | met | backend/migrations/V1.php:1 | ok" for i in item["items"]]
        return "\n".join(lines + ["S | fine", "TICKET-REVIEW: done"])
    seen = answers(monkeypatch, {"pr-reviewer": reviewer})
    # keel 0.18.68 standard reads a change of this size with one reviewer; full splits it by area
    s = wait(client, start(client, repo, workflow="review-pr", title="Review dispatch", request="branch: feat/dispatch\n" + TICKET,
                           data={"depth": "full"}))
    assert s["waiting"]["step"] == "share", s
    assert any(r.item["job"] == "cross" for r in seen if r.agent == "pr-reviewer")   # and one reviewer between files
    jobs = [r.item for r in seen if r.agent == "pr-reviewer" and r.item["job"] == "bugs"]
    areas = sorted(j["area"] for j in jobs)
    assert areas == ["deploy-config", "message-pipeline", "migrations", "persistence", "tests"], areas
    deploy = next(j for j in jobs if j["area"] == "deploy-config")
    assert any(x.startswith("backend/.env ") for x in deploy["files"]) and deploy["lenses"][0].startswith("config-secrets: Secrets and config")
    detail = s["waiting"]["detail"]
    assert "PARTIAL - not every file was read" in detail
    assert "NOT READ - the review is partial" in detail
    unread = [x for x in files if "Messaging" in x or "Outbox" in x]
    assert sum(1 for x in unread if x in detail.split("NOT READ", 1)[1].split("\n\n", 1)[0]) == 1
    assert "Read: 1 file(s) no reviewer read" in detail


def test_a_read_only_flow_never_runs_the_stack_setup(client, repo, monkeypatch):
    on = {"runs": 0}
    monkeypatch.setattr(stack_setup, "flow_needs", lambda root: ([], [{"stack": "php", "dir": "backend", "commands": ["x"],
                                                                        "why": "no vendor/", "key": "k"}]))

    def prepare(root, progress):
        on["runs"] += 1
        return {"ok": False, "note": "composer install failed", "detail": "", "jobs": []}
    monkeypatch.setattr(stack_setup, "prepare", prepare)
    _big_branch(repo)
    answers(monkeypatch, {})
    s = wait(client, start(client, repo, workflow="review-pr", title="Review", request="branch: feat/dispatch\n" + TICKET))
    assert s["waiting"]["step"] == "share" and on["runs"] == 0, s
    s = wait(client, start(client, repo, title="A feature"))            # a flow that builds still sets the stack up
    assert s["waiting"]["step"] == "__setup" and on["runs"] == 1, s


def test_read_receipts_in_the_research_format_and_where_with_words():
    p = ta.parse_lines("R | a.php | full\nR | b.php | hunks\nR | c.php | skipped | 3,000 lines\n"
                       "X | d.php:4 | not judged | the config is outside the repo | ask the author\n"
                       "F | blocking | m/V1.php (the status default) | old rows get label_generated | backfill")
    assert p["read"] == ["a.php", "b.php"] and p["gaps"] == [{"file": "c.php", "why": "3,000 lines"}]
    assert p["set_aside"][0]["file"] == "d.php"
    assert p["findings"][0]["file"] == "m/V1.php"                         # dedupe compares the path, not the words


def test_a_real_review_without_any_receipt_is_partial_not_done():
    results = [{"item": "bugs-1", "text": "F | minor | a.php:1 | x | y\nTICKET-REVIEW: done",
                "usage": {"tokens_in": 300_000, "tokens_cached": 0, "tokens_out": 10}}]
    r = ta._claims(_claims_input(results))
    assert not r.stop and r.update["data"]["ticket_review"]["partial"] is True


def test_an_env_reference_is_not_a_secret():
    from keel_engine.rules import checks
    assert checks.scan_secrets("        $secret: '%env(CARRIER_WEBHOOK_SECRET)%'") == []
    assert checks.scan_secrets("API_TOKEN=${API_TOKEN_FROM_VAULT}") == []
    assert checks.scan_secrets("password = 'q8Zr2LkVx9PwT4mN'")                 # a literal still is


def test_claims_over_the_cap_are_listed_as_not_checked_never_as_findings():
    text = "READ | a.php\n" + "\n".join(f"F | minor | a.php:{n * 20} | problem number {n} in branch{n} | fix {n}"
                                        for n in range(1, ta.MAX_CHECKS + 6))
    r = ta._claims(_claims_input([{"item": "bugs-1", "text": text, "usage": {"tokens_in": 300_000}}], files=("a.php",)))
    data = r.update["data"]
    assert sum(len(j["claims"]) for j in data["ticket_checks"]) == ta.MAX_CHECKS
    assert len(data["ticket_checks"]) == ta.MAX_CHECK_JOBS and all(len(j["claims"]) <= ta.CHECK_BATCH for j in data["ticket_checks"])
    review = data["ticket_review"]
    assert len(review["unchecked"]) == 5
    review["source"].update(branch="feat/x", base="main")
    rec = ta._record(ActionInput(root="/tmp", phase="review", title="t", ac=None, acs=[], fake=True, flow="review-pr",
                                 data={**data, "check_results": [], "settle_results": []}))
    out = rec.update["data"]["ticket_review"]
    assert out["findings"] == [] and len(out["not_checked"]) == ta.MAX_CHECKS + 5   # nobody answered: none is a finding
    assert out["verdict"] == "unclear"                                            # and it is not "done" either


def test_a_review_leaves_the_persons_folder_exactly_as_it_was(client, repo, monkeypatch):
    _big_branch(repo)
    git(repo, "checkout", "-q", "-b", "feat/my-work")
    (repo / "wip.txt").write_text("not committed\n")                    # the person's own work in progress

    def snapshot():
        return [git(repo, *args).stdout for args in (("rev-parse", "--abbrev-ref", "HEAD"), ("rev-parse", "HEAD"),
                                                     ("status", "--porcelain"), ("branch", "--list"),
                                                     ("worktree", "list", "--porcelain"))]
    before = snapshot()
    answers(monkeypatch, {})
    s = wait(client, start(client, repo, workflow="review-pr", title="Review", request="branch: feat/dispatch\n" + TICKET))
    assert s["waiting"]["step"] == "share", s
    assert snapshot() == before                                           # HEAD, branch, files, branches, worktrees
