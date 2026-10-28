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

READ = ["keel_review_prs", "keel_review_files", "keel_review_diff", "keel_review_notes"]
WRITE = ["keel_review_comment", "keel_review_reply", "keel_review_resolve", "keel_review_explain", "keel_review_findings", "keel_review_submit"]

VIEW = {"key": "pr:7", "kind": "pr", "number": 7, "title": "Save the page size", "branch": "feat/paging", "base": "main", "added": 9, "removed": 1,
        "approved": [], "changes_requested": ["bo"], "viewed": ["src/Prefs.kt"], "notes": [],
        "files": [{"path": "src/Prefs.kt", "status": "M", "added": 6, "removed": 1, "binary": False},
                  {"path": "src/PrefsTest.kt", "status": "A", "added": 3, "removed": 0, "binary": False}],
        "threads": [{"id": "th1", "path": "src/Prefs.kt", "line": 3, "resolved": False, "comments": [{"author": "bo", "body": "Config?"}]}],
        "drafts": [{"id": "dr1", "path": "src/Prefs.kt", "line": 4, "side": "RIGHT", "body": "Clear it"}]}


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
    assert {n for n in WRITE if tools[n].annotations.openWorldHint} == {"keel_review_reply", "keel_review_resolve", "keel_review_submit"}


def test_the_reads_say_what_a_reviewer_needs():
    s = ReviewApi()
    api = api_on(s)
    assert mcp_tools.review_prs(api, "shop") == "acme/shop · 1 pull request(s) (all)\n#7  Save the page size · ana · feat/paging → main · asks you"
    files = mcp_tools.review_files(api, "shop", pr=7)
    assert files.splitlines()[0] == "Pull request #7: Save the page size · feat/paging → main · +9 −1 · review key pr:7"
    assert "  M  src/Prefs.kt  +6 −1  (viewed)" in files and "  A  src/PrefsTest.kt  +3 −0" in files
    assert "  th1  src/Prefs.kt:3  open  bo: Config? (1 comment(s))" in files
    assert "  src/Prefs.kt:4 RIGHT  Clear it" in files and "changes asked by bo" in files
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
                                       ("keel_review_resolve", {"thread_id": "th1", "pr": 7})])
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
