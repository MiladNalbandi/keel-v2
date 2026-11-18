"""The Code Review plugin in keel's own MCP server (keel2 mcp, 0.18.6): its read tools at the read level, its acting
ones at write; a reply, a resolve and a submit post to GitHub or GitLab, so the person says yes first (through the
client). Each tool goes through the plugin's api routes (a stand-in api here). And its keel TUI part (contributes.tui:
the /review commands and the review mode) passes keel-plugin lint, and is the one keel TUI's tests use."""

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest
import yaml
from mcp.shared.memory import create_connected_server_and_client_session

from keel_engine import mcp_server
from keel_plugin_review import mcp_tools

ROOT = Path(__file__).resolve().parents[4]
MANIFEST = yaml.safe_load((ROOT / "plugins" / "review" / "keel-plugin.yml").read_text())

READ = ["keel_review_prs", "keel_review_files", "keel_review_diff", "keel_review_notes", "keel_review_threads", "keel_review_thread",
        "keel_review_conversation", "keel_review_file", "keel_review_symbol", "keel_review_diagrams"]
WRITE = ["keel_review_comment", "keel_review_reply", "keel_review_resolve", "keel_review_explain", "keel_review_findings", "keel_review_submit",
         "keel_review_draft", "keel_review_move_drafts", "keel_review_viewed", "keel_review_base", "keel_review_check", "keel_review_button",
         "keel_review_finding", "keel_review_address", "keel_review_fix_finding", "keel_review_checkout", "keel_review_merge"]
# what posts to the host, starts a flow or moves the project folder: the person says yes first
ASKS = {"keel_review_reply", "keel_review_resolve", "keel_review_submit", "keel_review_address", "keel_review_fix_finding",
        "keel_review_checkout", "keel_review_merge"}

VIEW = {"key": "pr:7", "kind": "pr", "number": 7, "title": "Save the page size", "branch": "feat/paging", "base": "main", "added": 9, "removed": 1,
        "approved": [], "changes_requested": ["bo"], "viewed": ["src/Prefs.kt"], "notes": [],
        "files": [{"path": "src/Prefs.kt", "status": "M", "added": 6, "removed": 1, "binary": False},
                  {"path": "src/PrefsTest.kt", "status": "A", "added": 3, "removed": 0, "binary": False}],
        "threads": [{"id": "th1", "path": "src/Prefs.kt", "line": 3, "resolved": False, "comments": [{"author": "bo", "body": "Config?"}]},
                    {"id": "th2", "path": "src/Prefs.kt", "line": 6, "start_line": 5, "side": "RIGHT", "resolved": False, "outdated": True,
                     "comments": [{"author": "bo", "at": "2026-11-18T09:00:00Z", "body": "This drops the size\nwhen it is 0.", "url": "https://x/1"},
                                  {"author": "ana", "at": "2026-11-18T10:00:00Z", "body": "fixed"}]}],
        "thread_groups": [["th1", "th2"]], "body": "Keeps the page size.", "state": "open", "author": "ana", "mergeable": True, "mine": True,
        "can_post": True, "checks": [{"name": "build", "state": "success", "url": None}],
        "commits": [{"sha": "abcdef1234", "message": "feat: page size\n\nmore", "author": "ana", "keel": True}],
        "conversation": [{"author": "bo", "at": "2026-11-18T08:00:00Z", "body": "Looks fine."}],
        "drafts": [{"id": "dr1", "path": "src/Prefs.kt", "line": 4, "side": "RIGHT", "body": "Clear it"}]}

AI = {"overview": None,
      "findings": {"status": "done", "result": {"findings": [
          {"id": "f1", "severity": "blocking", "path": "src/Prefs.kt", "line": 4, "title": "drops 0", "check": "confirmed", "why": "x"},
          {"id": "f2", "severity": "nit", "path": "src/Prefs.kt", "line": 5, "title": "name", "check": "confirmed", "why": "y"}]}},
      "decisions": {"f2": {"decision": "dismissed", "why": "style"}},
      "fixes": {"f1": {"flow_id": "fl9", "workflow": "fix"}},
      "judges": {"th2": {"status": "done", "stale": False, "result": {
          "stage": "done", "model": "anthropic · claude-haiku-5-5", "same_as": ["th1"], "reply": "You are right: 0 is dropped.",
          "items": [{"claim": "drops the size at 0", "verdict": "reviewer_right", "why": "Prefs.kt:6 checks > 0", "fix": "check >= 0"},
                    {"claim": "naming", "verdict": "author_right", "why": "matches Prefs.kt:2", "fix": ""}]}}},
      "fixed": {"th2": {"status": "done", "result": {"fixed": False, "why": "Prefs.kt:6 still checks > 0", "still_open": "the 0 case"}}},
      "addressed": {"th2": {"flow_id": "fl7", "workflow": "small", "branch": "feat/paging"}},
      "buttons": {"explain-it|th2": {"status": "done", "result": {"text": "It keeps the size."}}},
      "config": {"buttons": [{"id": "explain-it", "label": "Explain it", "mode": "ask"}, {"id": "spike", "label": "Spike", "mode": "workflow"}]}}


class ReviewApi:
    """keel's api with the Code Review plugin on for "shop" (or off)."""

    def __init__(self, on=("review",)):
        self.on = on
        self.calls: list[tuple[str, str, dict]] = []
        self.ai: dict | None = None

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path.removeprefix("/api")
        body = json.loads(req.content) if req.content else {}
        q = dict(req.url.params)
        self.calls.append((req.method, path, body or q))
        if path == "/projects":
            return httpx.Response(200, json=[{"id": "shop", "name": "shop", "root": "/nowhere"}])
        if path == "/projects/shop/plugins":
            return httpx.Response(200, json=[{"name": n, "enabled": True} for n in self.on])
        r = path.removeprefix("/projects/shop/review")
        if r == "/open":
            return httpx.Response(200, json={**VIEW, "key": f"pr:{q['ref']}" if q.get("ref", "").isdigit() else "branch:feat/paging",
                                              "kind": "pr" if q.get("ref", "").isdigit() else "branch"})
        if r == "/prs":
            return httpx.Response(200, json={"host": {"path": "acme/shop"}, "note": None, "prs": [
                {"number": 7, "title": "Save the page size", "author": "ana", "branch": "feat/paging", "base": "main", "review_requested": True}]})
        if r == "/diff":
            return httpx.Response(200, json={"diff": f"--- a/{q['path']}\n+++ b/{q['path']}\n@@ -1 +1 @@\n-a\n+b\n"})
        if r == "/file":
            return httpx.Response(200, json={"path": q["path"], "ref": "feat/paging" if q.get("side") == "head" else "main", "sha": "1234567890",
                                              "text": "\n".join(f"line {n}" for n in range(1, 451)), "truncated": False})
        if r in ("/definition", "/usages"):
            return httpx.Response(200, json={"symbol": q["symbol"], "ref": "feat/paging", "truncated": False, "places": [
                {"path": "src/Prefs.kt", "line": 2, "text": "fun size()", "declaration": r == "/definition", "test": False, "changed": True}]})
        if r == "/diagrams":
            return httpx.Response(200, json={"text": "1 table changes", "database": {"ascii": "[prefs] +size"}, "system": {"mermaid": "graph TD"}})
        if r == "/drafts/move":
            return httpx.Response(200, json={**VIEW, "drafts": VIEW["drafts"] * 2})
        if r.startswith("/drafts/") and req.method == "PUT":
            return httpx.Response(200, json={"id": r.rsplit("/", 1)[1], "path": "src/Prefs.kt", "line": 4, "body": body["body"]})
        if r.startswith("/drafts/") and req.method == "DELETE":
            return httpx.Response(200, json={"ok": True})
        if r == "/viewed":
            return httpx.Response(200, json={"viewed": ["src/Prefs.kt", body["path"]]})
        if r == "/base":
            return httpx.Response(200, json={**VIEW, "kind": "branch", "base": "origin/main", "base_choice": body["base"] or "remote"})
        if r.endswith("/address") or r.endswith("/fix"):
            return httpx.Response(200, json={"flow_id": "fl8", "title": "t", "branch": "feat/paging", "workflow": "small", "note": "It never pushes."})
        if r == "/checkout":
            return httpx.Response(200, json={"branch": "feat/paging", "note": "Your changes stayed."})
        if r == "/merge":
            return httpx.Response(200, json={"merged": True, "message": "Pull request merged", "view": VIEW})
        if r.startswith("/threads/") and ("/judge" in r or "/buttons/" in r) or r.startswith("/ai/findings/"):
            return httpx.Response(200, json={**AI, "judges": {**AI["judges"], "th1": {"status": "running"}}})
        if r == "/ai" and self.ai is not None:
            return httpx.Response(200, json=self.ai)
        if r == "/ai":
            return httpx.Response(200, json={"overview": {"status": "done", "result": {"summary": "Saves the size.", "risk": "low", "risk_why": "small",
                                                                                         "effort": 1, "files": [{"path": "src/Prefs.kt", "what": "a map"}]}},
                                              "findings": None})
        if r == "/drafts":
            return httpx.Response(200, json={"id": "dr2", **body})
        if r.startswith("/threads/") or r.startswith("/ai/"):
            return httpx.Response(200, json=VIEW)
        if r == "/submit":
            return httpx.Response(200, json={"posted": 1, "url": "https://github.com/acme/shop/pull/7#r1", "view": VIEW})
        return httpx.Response(404, json={"error": f"no {path}"})


def api_on(s: ReviewApi):
    return mcp_server.KeelApi("http://keel.test", transport=httpx.MockTransport(s))


def test_its_tools_join_keels_server_at_their_levels():
    assert mcp_server.part_tools() == {"read": READ, "write": WRITE}
    assert mcp_server.tool_level("keel_review_files") == "read" and mcp_server.tool_level("keel_review_submit") == "write"
    names = lambda level: [t.name for t in asyncio.run(mcp_server.build_server(level=level, api=object()).list_tools())]
    read, write = names("read"), names("write")
    assert [n for n in read if n.startswith("keel_review")] == READ
    assert [n for n in write if n.startswith("keel_review")] == READ + WRITE
    # the parts' read tools come after keel_dashboard, their acting ones after keel's own write tools, as 0.16.1 placed them
    assert read.index("keel_dashboard") < read.index("keel_review_prs") < read.index("keel_flows")
    assert write.index("keel_restore_doc") < write.index("keel_review_comment") < write.index("keel_stop_flow")
    tools = {t.name: t for t in asyncio.run(mcp_server.build_server(level="write", api=object()).list_tools())}
    assert tools["keel_review_files"].annotations.readOnlyHint and not tools["keel_review_comment"].annotations.readOnlyHint
    assert {n for n in WRITE if tools[n].annotations.openWorldHint} == {"keel_review_reply", "keel_review_resolve", "keel_review_submit",
                                                                          "keel_review_merge"}
    assert {n for n in WRITE if tools[n].annotations.destructiveHint} == {"keel_review_draft", "keel_review_merge"}


def test_the_reads_say_what_a_reviewer_needs():
    s = ReviewApi()
    api = api_on(s)
    assert mcp_tools.review_prs(api, "shop") == "acme/shop · 1 pull request(s) (all)\n#7  Save the page size · ana · feat/paging → main · asks you"
    files = mcp_tools.review_files(api, "shop", pr=7)
    assert files.splitlines()[0] == "Pull request #7: Save the page size · feat/paging → main · +9 −1 · review key pr:7"
    assert "  M  src/Prefs.kt  +6 −1  (viewed)" in files and "  A  src/PrefsTest.kt  +3 −0" in files
    assert "  th1  src/Prefs.kt:3  open  bo: Config? (1 comment(s))" in files
    assert "  dr1  src/Prefs.kt:4 RIGHT  Clear it" in files and "changes asked by bo" in files
    assert ("GET", "/projects/shop/review/open", {"ref": "7"}) in s.calls
    assert mcp_tools.review_files(api, "shop").startswith("Branch feat/paging:")                   # the branch you are on
    assert ("GET", "/projects/shop/review/open", {"ref": ""}) in s.calls
    diff = mcp_tools.review_diff(api, None, "shop", pr=7)
    assert diff.count("@@ -1 +1 @@") == 2 and "+++ b/src/PrefsTest.kt" in diff
    assert mcp_tools.review_diff(api, "src/Prefs.kt", "shop", pr=7).count("@@") == 2
    notes = mcp_tools.review_notes(api, "shop", pr=7)
    assert "  Saves the size." in notes and "  src/Prefs.kt: a map" in notes and "findings: none yet (keel_review_findings asks for them)" in notes


def test_the_acting_tools_go_through_the_plugins_routes():
    s = ReviewApi()
    api = api_on(s)
    assert mcp_tools.review_comment(api, "src/Prefs.kt", 4, "Clear it", "RIGHT", "shop", 7).startswith("Draft dr2 on src/Prefs.kt:4 saved in keel")
    assert ("POST", "/projects/shop/review/drafts", {"key": "pr:7", "path": "src/Prefs.kt", "line": 4, "side": "RIGHT", "body": "Clear it"}) in s.calls
    # a comment on several lines: start_line is its first line
    assert mcp_tools.review_comment(api, "src/Prefs.kt", 5, "Move these", "RIGHT", "shop", 7, start_line=3).startswith("Draft dr2 on src/Prefs.kt:3-5")
    assert ("POST", "/projects/shop/review/drafts", {"key": "pr:7", "path": "src/Prefs.kt", "line": 5, "side": "RIGHT", "body": "Move these",
                                                     "start_line": 3, "start_side": "RIGHT"}) in s.calls
    assert mcp_tools.review_reply(api, "th1", "Yes", "shop", 7) == "Replied on thread th1 of pull request #7."
    assert mcp_tools.review_resolve(api, "th1", True, "shop", 7) == "Thread th1 of pull request #7 is resolved."
    assert "keel_review_notes shows it" in mcp_tools.review_ai(api, "findings", "shop", 7)
    assert ("POST", "/projects/shop/review/ai/findings", {"key": "pr:7"}) in s.calls
    assert mcp_tools.review_submit(api, "APPROVE", "", "shop", 7) == \
        "Sent your review (APPROVE) on pull request #7 with 1 line comment(s): https://github.com/acme/shop/pull/7#r1"
    off = api_on(ReviewApi(on=()))
    with pytest.raises(mcp_server.ApiError, match="The Code Review plugin is off for shop"):
        mcp_tools.review_files(off, "shop")


@pytest.mark.parametrize("tool,args", [("keel_review_submit", {"event": "APPROVE", "pr": 7}), ("keel_review_reply", {"thread_id": "th1", "body": "x", "pr": 7}),
                                       ("keel_review_resolve", {"thread_id": "th1", "pr": 7}), ("keel_review_address", {"thread_id": "th2", "pr": 7}),
                                       ("keel_review_fix_finding", {"finding_id": "f1", "pr": 7}), ("keel_review_checkout", {"pr": 7}),
                                       ("keel_review_merge", {"pr": 7})])
def test_what_posts_to_the_host_asks_the_person_first(tool, args):
    """A client that cannot ask the person is refused (no --yes), and nothing is posted; with --yes it goes through."""
    s = ReviewApi()

    async def call(yes: bool) -> str:
        srv = mcp_server.build_server(level="write", api=api_on(s), yes=yes)
        async with create_connected_server_and_client_session(srv._mcp_server) as client:
            r = await client.call_tool(tool, {**args, "project": "shop"})
            return r.content[0].text

    assert "cannot ask you to confirm" in asyncio.run(call(False))
    # 0.18.64 only the question in keel's Inbox (the person's OK out of the model's reach), nothing to the host
    assert not [c for c in s.calls if c[0] == "POST" and not c[1].endswith("/plugins/ask")]
    assert not asyncio.run(call(True)).startswith("keel:")
    assert [c for c in s.calls if c[0] == "POST"]


def test_a_draft_needs_no_question():
    s = ReviewApi()

    async def call() -> str:
        srv = mcp_server.build_server(level="write", api=api_on(s))
        async with create_connected_server_and_client_session(srv._mcp_server) as client:
            r = await client.call_tool("keel_review_comment", {"path": "src/Prefs.kt", "line": 4, "body": "Clear it", "pr": 7, "project": "shop"})
            return r.content[0].text

    assert asyncio.run(call()).startswith("Draft dr2")
    post = next(c for c in s.calls if c[0] == "POST")
    assert post[1] == "/projects/shop/review/drafts"


# ------------------------------------------------------------------ its keel TUI part

def test_its_tui_part_passes_lint_and_is_the_one_keel_tuis_tests_use():
    sys.path.insert(0, str(ROOT / "tools" / "keel-plugin"))
    from keel_plugin.manifest import _Check, _tui, load

    data = load((ROOT / "plugins" / "review" / "keel-plugin.yml").read_text())
    c = _Check()
    _tui(c, "review", data["contributes"]["tui"])
    assert c.errors == []
    assert data["contributes"]["tui"] == MANIFEST["contributes"]["tui"]          # keel-plugin's reader and PyYAML agree
    assert json.loads((ROOT / "cli" / "test" / "review-tui.json").read_text()) == MANIFEST["contributes"]["tui"]
    mode = MANIFEST["contributes"]["tui"]["modes"][0]
    assert mode["name"] == "review" and list(mode["state"]) == ["view", "ai"]
    assert {k["key"] for k in mode["keys"]} == set("crxvefoFdspg")
    # every key that changes something asks first: a yes / no, or the person's own words
    assert all(k.get("confirm") or k.get("ask") for k in mode["keys"] if (k.get("call") or {}).get("method") == "POST")


def test_a_partial_ai_run_says_which_files_it_did_not_read():
    # keel 0.18.57: the overview and the findings name what no reviewer read (ReviewAiService coverage)
    full = {"partial": False, "coverage": {"files": 3, "read": 3, "unread": [], "known": True}}
    assert mcp_tools._coverage(full) == ["  read all 3 changed file(s)"]
    part = {"partial": True, "coverage": {"files": 70, "read": 24, "unread": [f"m/V{n}.php" for n in range(46)], "known": True}}
    line = mcp_tools._coverage(part)[0]
    assert line.startswith("  PARTIAL: read 24 of 70 changed file(s); not read: m/V0.php, m/V1.php") and line.endswith("… 26 more")
    assert mcp_tools._coverage({"coverage": {"files": 3, "read": None, "known": False}}) == []
    assert mcp_tools._coverage({}) == []


def test_notes_list_what_nobody_could_check_apart_from_the_findings():
    s = ReviewApi()
    api = api_on(s)
    s.ai = {"findings": {"status": "done", "result": {
        "findings": [{"severity": "blocking", "path": "src/A.kt", "line": 3, "title": "drops rows", "check": "confirmed", "why": "x"}],
        "not_checked": [{"path": "src/B.kt", "line": 9, "title": "callers may pass null",
                         "check_why": "its reviewer could not see the code it is about"}]}}}
    notes = mcp_tools.review_notes(api, "shop", pr=7)
    assert "findings: done · 1 found" in notes
    assert "not checked (1): no proof either way" in notes and "src/B.kt:9  callers may pass null - its reviewer" in notes


# ------------------------------------------------------------------ every thread, and what keel added on it (#198)

def test_threads_say_what_keel_added_on_each():
    s = ReviewApi()
    s.ai = AI
    out = mcp_tools.review_threads(api_on(s), "shop", 7)
    assert out.splitlines()[0] == "Pull request #7: 2 thread(s) (keel_review_thread reads one in full)"
    assert "th1  src/Prefs.kt:3  open  bo: Config? (1 comment(s))\n    keel: not checked (keel_review_check)" in out
    assert "th2  src/Prefs.kt:5-6  open outdated  bo: This drops the size when it is 0. (2 comment(s))" in out
    assert "    last: ana: fixed" in out
    assert "    keel: 1× the reviewer is right, 1× the author is right · a reply suggested" in out
    assert '    keel checked the author\'s "fixed": NOT fixed · Prefs.kt:6 still checks > 0 · still open: the 0 case' in out
    assert "    Address it: flow fl7 (small on feat/paging)" in out
    assert out.endswith("threads that make the same point (keel checks each group once): th1, th2")


def test_one_thread_in_full():
    s = ReviewApi()
    s.ai = AI
    out = mcp_tools.review_thread(api_on(s), "th2", "shop", 7)
    assert out.splitlines()[0] == "Thread th2 on pull request #7 · src/Prefs.kt:5-6 RIGHT · open outdated"
    assert "--- bo · 2026-11-18T09:00:00Z · https://x/1\nThis drops the size\nwhen it is 0." in out     # the whole comment
    assert "--- ana · 2026-11-18T10:00:00Z\nfixed" in out
    assert "  - the reviewer is right: drops the size at 0\n    why: Prefs.kt:6 checks > 0\n    fix: check >= 0" in out
    assert "  - the author is right: naming\n    why: matches Prefs.kt:2" in out
    assert "  suggested reply (keel_review_reply posts it):\n    You are right: 0 is dropped." in out
    assert "  checked with anthropic · claude-haiku-5-5" in out and "  the same point as: th1" in out
    assert "button explain-it (Explain it): done\n    It keeps the size." in out
    assert "button spike (Spike, workflow): not used (keel_review_button)" in out
    with pytest.raises(mcp_server.ApiError, match="No thread th9 on pull request #7"):
        mcp_tools.review_thread(api_on(s), "th9", "shop", 7)
    with pytest.raises(mcp_server.ApiError, match="A branch has no threads"):
        mcp_tools.review_threads(api_on(s), "shop")


def test_conversation_file_symbol_and_diagrams():
    s = ReviewApi()
    api = api_on(s)
    c = mcp_tools.review_conversation(api, "shop", 7)
    assert c.splitlines()[0] == "Pull request #7: Save the page size · ana · feat/paging → main · open"
    assert "mergeable: True · yours: keel_review_merge can merge it" in c and "--- description\nKeeps the page size." in c
    assert "  success  build" in c and "  abcdef12  feat: page size · ana · keel" in c and "  bo · 2026-11-18T08:00:00Z\n    Looks fine." in c
    f = mcp_tools.review_file(api, "src/Prefs.kt", "head", 1, None, "shop", 7)
    assert f.splitlines()[0] == "src/Prefs.kt at head (feat/paging 12345678) · lines 1-400 of 450"
    assert f.splitlines()[1] == "  1  line 1" and f.endswith("… 50 more line(s): ask with start_line=401")
    assert mcp_tools.review_file(api, "src/Prefs.kt", "base", 440, 445, "shop", 7).splitlines()[1:] == [f"{n}  line {n}" for n in range(440, 446)]
    assert ("GET", "/projects/shop/review/file", {"key": "pr:7", "path": "src/Prefs.kt", "side": "base"}) in s.calls
    assert mcp_tools.review_symbol(api, "size", "usages", "shop", 7) == \
        "Usages of size at feat/paging: 1\n  src/Prefs.kt:2  fun size() · changed here"
    assert "· declaration" in mcp_tools.review_symbol(api, "size", "definition", "shop", 7)
    d = mcp_tools.review_diagrams(api, "both", "shop", 7)
    assert "1 table changes" in d and "--- database\n[prefs] +size" in d and "--- system\ngraph TD" in d
    assert "--- system" not in mcp_tools.review_diagrams(api, "database", "shop", 7)


def test_notes_give_finding_ids_your_decisions_and_fix_flows():
    s = ReviewApi()
    s.ai = AI
    notes = mcp_tools.review_notes(api_on(s), "shop", pr=7)
    assert "  f1  blocking  src/Prefs.kt:4  drops 0 (confirmed) · Fix it flow fl9" in notes
    assert "  f2  nit  src/Prefs.kt:5  name (confirmed) · you dismissed: style" in notes


def test_the_new_acting_tools_go_through_the_plugins_routes():
    s = ReviewApi()
    api = api_on(s)
    p = "/projects/shop/review"
    assert mcp_tools.review_draft(api, "dr1", "Clear it now", project="shop") == "Draft dr1 on src/Prefs.kt:4 now reads: Clear it now"
    assert ("PUT", f"{p}/drafts/dr1", {"body": "Clear it now"}) in s.calls
    assert mcp_tools.review_draft(api, "dr1", delete=True, project="shop") == "Draft dr1 deleted."
    assert ("DELETE", f"{p}/drafts/dr1", {}) in s.calls
    with pytest.raises(mcp_server.ApiError, match="Give the new text"):
        mcp_tools.review_draft(api, "dr1", " ", project="shop")
    assert mcp_tools.review_move_drafts(api, "shop", pr=7).startswith("The drafts of branch feat/paging are now on pull request #7 (2 draft(s)")
    assert ("POST", f"{p}/drafts/move", {"from": "branch:feat/paging", "to": "pr:7"}) in s.calls
    assert mcp_tools.review_viewed(api, "src/PrefsTest.kt", True, "shop", 7).endswith("2 file(s) viewed in pull request #7.")
    assert ("POST", f"{p}/viewed", {"key": "pr:7", "path": "src/PrefsTest.kt", "viewed": True}) in s.calls
    assert mcp_tools.review_base(api, "local", "shop").startswith("Branch feat/paging compares against origin/main (local)")
    assert ("PUT", f"{p}/base", {"key": "branch:feat/paging", "base": "local"}) in s.calls
    assert mcp_tools.review_check(api, "th1", project="shop", pr=7).startswith("Thread th1: keel is checking it.")
    assert ("POST", f"{p}/threads/th1/judge", {"key": "pr:7", "again": False, "harder": False}) in s.calls
    every = mcp_tools.review_check(api, None, True, False, "shop", 7)
    assert ("POST", f"{p}/threads/judge", {"key": "pr:7", "again": True, "harder": False}) in s.calls
    assert "  th1: keel is checking it" in every and "  th2: 1× the reviewer is right" in every
    s.ai = AI
    assert mcp_tools.review_button(api, "th2", "Explain it", project="shop", pr=7).startswith("Explain it on thread th2: done")
    assert ("POST", f"{p}/threads/th2/buttons/explain-it", {"key": "pr:7", "again": False, "harder": False}) in s.calls
    with pytest.raises(mcp_server.ApiError, match="No thread button nope. The buttons: explain-it \\(Explain it\\), spike"):
        mcp_tools.review_button(api, "th2", "nope", project="shop", pr=7)
    assert mcp_tools.review_finding(api, "f2", "dismissed", "style", "shop", 7) == "Finding f2 of pull request #7: dismissed (style)."
    assert ("POST", f"{p}/ai/findings/f2", {"key": "pr:7", "decision": "dismissed", "why": "style"}) in s.calls
    assert mcp_tools.review_address(api, "th2", "keep 0", None, None, "shop", 7) == \
        "Address it on thread th2: flow fl8 (small) on feat/paging · It never pushes. keel_flow reads it."
    assert ("POST", f"{p}/threads/th2/address", {"key": "pr:7", "note": "keep 0", "workflow": None, "fix": None}) in s.calls
    assert mcp_tools.review_fix_finding(api, "f1", None, "shop", 7).startswith("Fix it on finding f1: flow fl8")
    assert ("POST", f"{p}/ai/findings/f1/fix", {"key": "pr:7", "workflow": None}) in s.calls
    assert mcp_tools.review_checkout(api, "shop", 7) == "The project folder is on feat/paging. Your changes stayed."
    assert mcp_tools.review_merge(api, "squash", True, "shop", 7) == "Merged pull request #7 (squash): Pull request merged"
    assert ("POST", f"{p}/merge", {"key": "pr:7", "method": "squash", "delete_branch": True}) in s.calls


def test_keel_only_actions_need_no_question():
    """A draft change, a viewed mark, a check or a decision changes only keel: no question, as for keel_review_comment."""
    s = ReviewApi()

    async def call(tool, args) -> str:
        srv = mcp_server.build_server(level="write", api=api_on(s))
        async with create_connected_server_and_client_session(srv._mcp_server) as client:
            r = await client.call_tool(tool, {**args, "project": "shop"})
            return r.content[0].text

    assert asyncio.run(call("keel_review_viewed", {"path": "src/Prefs.kt", "pr": 7})).startswith("src/Prefs.kt is viewed")
    assert asyncio.run(call("keel_review_check", {"thread_id": "th1", "pr": 7})).startswith("Thread th1:")
    assert not [c for c in s.calls if c[1].endswith("/plugins/ask")]
