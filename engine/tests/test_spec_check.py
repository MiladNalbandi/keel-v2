import pytest

from keel_engine.runtime import spec_check
from conftest import decide, start, wait

GOOD = """# Leaderboard

## In plain words
Players cannot see where they stand. We will show a leaderboard, like the table in a sports club. Scores stay the same.

## For engineers
- Inbound: a new GET /leaderboard in LeaderboardController; the domain sorts by score, ties share a rank.
- Persistence: reads the scores table only; no transaction, no ordering concern.

## What will change
- the leaderboard page and its endpoint: new

## Payload changes
none: GET /leaderboard is new, no existing payload changes shape.

## Database changes
none: the scores table is only read.

## Architecture
```
[GET /leaderboard new] -> [ranking new] -> [scores unchanged]
```
It follows the controller, service, repository layers.

## Request
Show ranks.

## Goal
Players see where they stand, so they come back to climb.

## User stories
- As a player, I want to see my rank, so that I know where I stand.

## Decisions
- user means a named entity

## Findings (evidence)
- Scores are stored per player in `src/scores/store.py:12`; no endpoint reads them yet.

## Acceptance criteria
- **AC-1** [API] Given scores, when GET /leaderboard is called, then it returns players ordered by rank.
- **AC-2** [WEB] Given the page, when it loads, then every player's rank is shown.

## Test layers
- AC-1: unit (the ranking), integration (GET /leaderboard); e2e: not needed
- AC-2: component test; e2e: not needed, one screen

## Validation and security rules
- Only public names are returned.

## Edge cases
- No players: an empty list.

## Risks and cost
- Two files, no migration, no deploy order.

## Definition of done
- [ ] Every criterion is green on its layers.

## Mockup
```
loading: [ spinner ]   empty: "No players yet"   filled: 1 ana 9   error: "Could not load"
```

## Request path
```
GET /leaderboard   NEW
```

## Out of scope
- login
"""
ACS = [{"id": "AC-1", "layer": "API", "title": "Given scores, when GET /leaderboard is called, then it returns players ordered by rank."},
       {"id": "AC-2", "layer": "WEB", "title": "Given the page, when it loads, then every player's rank is shown."}]


def test_a_complete_spec_has_nothing_missing():
    assert [f for f in spec_check.check(GOOD, ACS) if f["level"] == "fix"] == []
    assert spec_check.describe(spec_check.check(GOOD, ACS)) == "Spec check: nothing missing."


def test_the_six_first_sections_are_required_and_none_needs_a_reason():
    for name in ("In plain words", "For engineers", "What will change", "Payload changes", "Database changes", "Architecture"):
        cut = GOOD.split(f"## {name}\n", 1)
        cut = cut[0] + "## " + cut[1].split("## ", 1)[1]
        texts = [f["text"] for f in spec_check.check(cut, ACS) if f["level"] == "fix"]
        assert any(f"No “{name}” section" in t for t in texts), (name, texts)
    bare = GOOD.replace("none: the scores table is only read.", "none")
    texts = [f["text"] for f in spec_check.check(bare, ACS) if f["level"] == "fix"]
    assert texts == ["“Database changes” says only none: add the reason in one line (\"none: <why>\")."]
    assert spec_check.SECTION_ORDER[:6] == ("In plain words", "For engineers", "What will change", "Payload changes",
                                            "Database changes", "Architecture")


def test_the_template_and_the_skill_start_with_the_six_sections():
    from pathlib import Path

    tpl = Path(spec_check.template_path()).read_text()
    heads = [h for h in spec_check.SECTION.findall(tpl)]
    assert heads[:7] == ["In plain words", "For engineers", "What will change", "Payload changes", "Database changes",
                         "Architecture", "Request"]
    skill = (Path(spec_check.template_path()).parent.parent / "skills" / "spec-writing" / "SKILL.md").read_text()
    assert skill.index("## In plain words") < skill.index("## Architecture") < skill.index("## Request ")


def test_a_screen_without_a_mockup_and_an_api_without_a_path_are_found():
    bad = GOOD.split("## Mockup")[0] + "## Out of scope\n- login\n"
    texts = [f["text"] for f in spec_check.check(bad, ACS) if f["level"] == "fix"]
    assert any("no Mockup" in t for t in texts) and any("no Request path" in t for t in texts)
    partial = GOOD.replace('empty: "No players yet"   filled: 1 ana 9   error: "Could not load"', "just a table")
    assert any("four states" in f["text"] for f in spec_check.check(partial, ACS))


def test_vague_and_incomplete_criteria_are_noted_not_blocked():
    acs = [{"id": "AC-1", "layer": "API", "title": "Given ties, when ranked, then either order is acceptable."},
           {"id": "AC-2", "layer": "API", "title": "The endpoint is fast."}]
    notes = [f["text"] for f in spec_check.check(GOOD, acs)]
    assert any("AC-1 is vague" in n for n in notes) and any("AC-2 has no “then”" in n for n in notes)
    assert all(f["level"] == "note" for f in spec_check.check(GOOD, acs) if "AC-" in f["text"])


def test_the_engine_sends_a_spec_back_once_when_something_clear_is_missing(client, repo, monkeypatch):
    from keel_engine.models import fake as fake_mod
    real = fake_mod._plan
    feedback = []

    def plan(req):
        if req.agent == "explorer" and req.phase == "spec" and req.step_name == "spec":
            feedback.append(req.feedback)
            path, content, answer, data = real(req)
            if len(feedback) == 1:      # the first spec has a [WEB] criterion and no mockup
                acs = [{"id": "AC-1", "layer": "WEB", "title": "Given the page, when it loads, then the rank shows."}]
                text = "# T\n\n## Acceptance criteria\n- **AC-1** [WEB] Given the page, when it loads, then the rank shows.\n"
                return path, text, answer, {"acs": acs, "spec": path}
            return path, content, answer, data
        return real(req)
    monkeypatch.setattr(fake_mod, "_plan", plan)
    tid = start(client, repo, acs=None, settings={"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause", "spec_check": True})
    s = wait(client, tid)
    assert len(feedback) == 2 and "spec check found problems" in feedback[1] and "no Mockup" in feedback[1]
    assert "No “Goal” section" in feedback[1] and "No “Test layers” section" in feedback[1]
    assert s["waiting"]["step"] == "spec_gate"
    assert "Spec check" in s["waiting"]["detail"]


def test_cases_of_one_rule_split_into_criteria_are_sent_back_to_be_merged():
    # Real feature run: three criteria differing only in the percent; the first code already met all of them.
    acs = [{"id": "AC-5", "title": "Given a cart, when applyCoupon(cart, 50.5) is called, then it throws an error naming percent"},
           {"id": "AC-6", "title": "Given a cart, when applyCoupon(cart, -1) is called, then it throws an error naming percent"},
           {"id": "AC-7", "title": "Given a cart, when applyCoupon(cart, 101) is called, then it throws an error naming percent"},
           {"id": "AC-1", "title": "Given a cart with total 100, when applyCoupon(cart, 50) is called, then it returns 50"},
           {"id": "AC-2", "title": "Given the player's best score, when the user's rank is read, then it is shown"},
           {"id": "AC-3", "title": "Given a cart, when an item 'apple' is added twice, then its quantity is the sum"}]
    found = [f for f in spec_check.check("", acs) if "cases of one rule" in f["text"]]
    assert len(found) == 1 and found[0]["level"] == "fix"
    assert found[0]["text"].startswith("AC-5, AC-6, AC-7 differ only in their values")


def fixes(text, acs=ACS, outside=False, root=None):
    return [f["text"] for f in spec_check.check(text, acs, outside, root) if f["level"] == "fix"]


def test_a_spec_without_the_templates_sections_is_sent_back():
    """keel v1's template (content/templates/spec.md): Goal, User stories, Findings, Test layers, Validation and security
    rules, Edge cases, Risks and cost and Definition of done are asked for; the old thin layout is not enough."""
    thin = GOOD.split("## Goal")[0] + "## Acceptance criteria" + GOOD.split("## Acceptance criteria")[1].split("## Test layers")[0] \
        + "## Mockup" + GOOD.split("## Mockup")[1]
    found = fixes(thin)
    for name in ("Goal", "User stories", "Findings (evidence)", "Test layers", "Validation and security rules", "Edge cases",
                 "Risks and cost", "Definition of done"):
        assert any(f"No “{name}” section" in t for t in found), name
    assert not any("“Acceptance criteria”" in t for t in found)
    # a heading with only the template's comment is empty
    assert any("No “Goal” section" in t for t in fixes(GOOD.replace("Players see where they stand, so they come back to climb.",
                                                                  "<!-- why this exists -->")))


def test_every_criterion_needs_a_test_layer_and_stories_their_form():
    no_layer = GOOD.replace("- AC-2: component test; e2e: not needed, one screen\n", "")
    assert any(t.startswith("Test layers names no layer for AC-2") for t in fixes(no_layer))
    padded = [dict(a, id=a["id"].replace("AC-", "AC-00")) for a in ACS]          # AC-001 in the criteria, AC-1 in the layers
    assert not any("Test layers" in t for t in fixes(GOOD, padded))
    assert any("As a <role>, I want" in t for t in fixes(GOOD.replace("- As a player, I want to see my rank, so that I know "
                                                                     "where I stand.", "- Players see ranks.")))


def test_outside_facts_need_unverified_and_open_questions():
    assert not any("Unverified" in t for t in fixes(GOOD))
    assert any("Unverified and open questions" in t for t in fixes(GOOD, outside=True))
    cited = GOOD.replace("no endpoint reads them yet.", "no endpoint reads them yet; the payload: https://docs.example.com/eta")
    assert any("Unverified and open questions" in t for t in fixes(cited))
    answered = cited + "\n## Unverified and open questions\n- Unverified: the ETA is UTC; confirm with the vendor.\n"
    assert not any("Unverified" in t for t in fixes(answered))
    assert spec_check.outside_facts("", {"web_docs_text": "CONFIRMED ..."}) and not spec_check.outside_facts("Show ranks", {})
    assert spec_check.outside_facts("", {"seed": {"evidence": "see https://developers.example.com/webhooks"}})


def test_the_gate_names_what_is_missing_apart_from_the_warnings():
    shown = spec_check.describe([{"level": "fix", "text": "No “Goal” section"}, {"level": "note", "text": "AC-1 is vague"}])
    missing, warnings = shown.split("Spec check (warnings, you decide):")
    assert "Spec check, missing" in missing and "No “Goal” section" in missing and "AC-1 is vague" in warnings


def test_keels_spec_template_has_every_section_the_check_asks_for():
    text = open(spec_check.template_path()).read()
    names = [m.lower() for m in spec_check.sections(text)]
    for n in spec_check.SECTION_ORDER:
        assert n.lower() in names, n
    assert names.index("goal") < names.index("acceptance criteria") < names.index("test layers") < names.index("plan")


def test_e2e_and_smoke_criteria_pass_the_check_without_a_mockup_or_request_path():
    """[E2E] and [SMOKE] are real criteria tags (spec-writing, the template, the parser): the check asks a [WEB] criterion
    for a mockup and an [API] one for a request path, never an [E2E] or [SMOKE] one."""
    from keel_engine.runtime import prompts

    text = GOOD.replace("- **AC-2** [WEB] Given the page, when it loads, then every player's rank is shown.",
                        "- **AC-2** [E2E] Given the page, when a player signs in, then their rank is shown.\n"
                        "- **AC-3** [SMOKE] Given a deploy, when GET /leaderboard is called, then it answers 200.")
    text = text.replace("- AC-2: component test; e2e: not needed, one screen", "- AC-2: e2e (sign in, open the board)\n- AC-3: smoke")
    acs = [{"id": m.group(1), "layer": m.group(2).upper(), "title": m.group(3)} for m in map(prompts.AC_LINE.search, text.splitlines()) if m]
    assert [a["layer"] for a in acs] == ["API", "E2E", "SMOKE"]
    texts = [f["text"] for f in spec_check.check(text, [a for a in acs if a["layer"] != "API"]) if f["level"] == "fix"]
    assert not any("Mockup" in t or "Request path" in t for t in texts), texts
    assert "[E2E]" in open(spec_check.template_path()).read()


def test_an_amendment_can_retag_a_criterion_as_e2e():
    from keel_engine.runtime import feature_actions

    block = "### 2026-10-19 — AC-2\nWas: - **AC-2** [WEB] the board shows\nNow:\n- **AC-2** [E2E] the board shows after sign-in\nWhy: a journey."
    assert feature_actions.amended_criteria(block) == {"AC-2": {"layer": "E2E", "title": "the board shows after sign-in"}}


# ---------------------------------------------------------------- 0.18.64 citations (field report: a spec cited lines
# 235-245 of a 38-line file and passed)

def _project(tmp_path, repo=True):
    import subprocess

    root = tmp_path / "shop"
    (root / "src" / "billing").mkdir(parents=True)
    (root / "src" / "orders.py").write_text("".join(f"line {n}\n" for n in range(1, 41)))      # 40 lines
    (root / "src" / "billing" / "Invoice.java").write_text("class Invoice {}\n")
    (root / "src" / "scores").mkdir()
    (root / "src" / "scores" / "store.py").write_text("".join(f"x = {n}\n" for n in range(20)))  # 20 lines
    if repo:
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    return root


def test_citations_are_found_in_every_form_and_urls_times_and_versions_are_not():
    from keel_engine.runtime import citations

    text = ("See `src/orders.py:88`, src/orders.py:10-12, src/orders.py#L5 and src/orders.py#L5-L9 (also src/orders.py:3:7).\n"
            "Not: https://example.com/a.py:3, http://localhost:8080, at 13:14, version 1.2:3, host:5432, `v2.1:4`.")
    got = [(c["rel"], c["line"], c["end"], c["quoted"]) for c in citations.find(text)]
    assert got == [("src/orders.py", 88, None, True), ("src/orders.py", 10, 12, False), ("src/orders.py", 5, None, False),
                   ("src/orders.py", 5, 9, False), ("src/orders.py", 3, None, False)]
    assert [c["rel"] for c in citations.find(text, quoted=True)] == ["src/orders.py"]


def test_a_missing_file_and_a_line_past_the_end_are_found_against_the_project(tmp_path):
    from keel_engine.runtime import citations

    root = str(_project(tmp_path))
    r = citations.check(root, "Fine: src/orders.py:40, `src/orders.py:1-40`, Invoice.java:1. Bad: `src/orders.py:88-95`, "
                              "src/gone.py:3, .../billing/Invoice.java:7. Not ours: example.com:8080, /etc/hosts.txt:1.")
    assert r["checked"] == 6
    assert [b["problem"] for b in r["bad"]] == [
        "src/orders.py:88-95 does not exist: the file has 40 lines",
        "src/gone.py:3 does not exist: no such file",
        ".../billing/Invoice.java:7 does not exist: the file has 1 line (src/billing/Invoice.java)"]
    assert {b["level"] for b in r["bad"]} == {"fix"}
    # not a git repo: the folder's files are read instead
    plain = str(_project(tmp_path / "plain", repo=False))
    assert [b["cite"] for b in citations.check(plain, "src/orders.py:41 and src/orders.py:40")["bad"]] == ["src/orders.py:41"]


def test_the_spec_check_sends_back_a_spec_whose_citations_do_not_exist(tmp_path):
    root = str(_project(tmp_path))
    assert fixes(GOOD, root=root) == []
    bad = GOOD.replace("`src/scores/store.py:12`", "`src/scores/store.py:235-245`")
    assert fixes(bad, root=root) == ["src/scores/store.py:235-245 does not exist: the file has 20 lines."]
    assert fixes(bad) == []                                       # without the project's root nothing is checked
    many = GOOD + "\n".join(f"- src/orders.py:{100 + n}" for n in range(12))
    texts = fixes(many, root=root)
    assert len(texts) == 11 and texts[-1].startswith("2 more citations do not resolve")


def test_the_spec_check_of_one_file_reads_its_criteria(tmp_path):
    root = _project(tmp_path)
    (root / "docs").mkdir()
    (root / "docs" / "orders.md").write_text(GOOD.replace("`src/scores/store.py:12`", "src/orders.py:50"))
    r = spec_check.check_file(str(root), "docs/orders.md")
    assert r["criteria"] == ["AC-1", "AC-2"] and r["fix"] == 1
    assert "src/orders.py:50 does not exist: the file has 40 lines." in r["summary"]


class _Api:
    """keel's api as keel_spec_check and keel_citations_check call it: here straight on to the engine's routes, with
    the project's folder the way the api adds it (SpecCheckController)."""

    def __init__(self, client, root):
        self.client, self.root = client, str(root)

    def get(self, path, **_):
        assert path == "/projects"
        return [{"id": "shop", "root": self.root}]

    def post(self, path, body):
        from keel_engine.mcp_api import ApiError

        r = self.client.post(path.replace("/projects/shop", ""), json={"root": self.root, **body})
        if r.status_code != 200:
            raise ApiError(r.json().get("error") or r.text, r.status_code)
        return r.json()


def test_the_mcp_tools_check_a_spec_and_the_citations_of_a_file_or_a_text(client, tmp_path):
    from keel_engine import mcp_more
    from keel_engine.mcp_api import ApiError

    root = _project(tmp_path)
    (root / "docs").mkdir()
    (root / "docs" / "orders.md").write_text(GOOD.replace("`src/scores/store.py:12`", "`src/orders.py:88-95`"))
    api = _Api(client, root)
    out = mcp_more.spec_check(api, "docs/orders.md", "shop")
    assert out.startswith("docs/orders.md: 2 acceptance criteria (AC-1, AC-2) · 1 to fix")
    assert "fix   src/orders.py:88-95 does not exist: the file has 40 lines." in out
    out = mcp_more.citations_check(api, path="docs/orders.md", project="shop")
    assert out == ("docs/orders.md: 1 citations of project files checked, 1 do not resolve:\n"
                   "fix   src/orders.py:88-95 does not exist: the file has 40 lines")
    assert mcp_more.citations_check(api, text="src/orders.py:40", project="shop").endswith("0 do not resolve.")
    # refused: a path outside the project, a file that is not there, a secret file, both a path and a text
    for path in ("../x.md", "/etc/passwd"):
        with pytest.raises(ApiError, match="not a path inside the project"):
            mcp_more.spec_check(api, path, "shop")
    with pytest.raises(ApiError, match="No file docs/gone.md"):
        mcp_more.spec_check(api, "docs/gone.md", "shop")
    with pytest.raises(ApiError, match="holds secrets"):
        mcp_more.citations_check(api, path=".env", project="shop")
    with pytest.raises(ApiError, match="one of them"):
        mcp_more.citations_check(api, path="docs/orders.md", text="x", project="shop")
