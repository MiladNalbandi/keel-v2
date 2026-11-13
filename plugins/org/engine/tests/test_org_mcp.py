"""#98 the Org plugin's tools in keel's MCP server (keel2 mcp): listed at their levels, and run through keel's api
against the Org part's real routes (the api's /api/org/* is the engine's /org/*): templates, save, list, show, a
mission given to a role, followed while its report waits for the person, and stopped."""

import asyncio

import httpx
import pytest
from conftest import wait_for, wait_status
from keel_engine import mcp_levels, mcp_server
from keel_engine.mcp_api import ApiError, KeelApi

from keel_plugin_org import config
from keel_plugin_org import mcp_tools as org_mcp

ORG = config.EXAMPLE.replace("projects: [shop]", "projects: [demo]").replace("autonomy: propose", "autonomy: act")
READ = ["keel_org_list", "keel_org_show", "keel_org_templates", "keel_org_missions", "keel_org_mission"]
WRITE = ["keel_org_save", "keel_org_give_mission", "keel_org_stop_mission", "keel_org_accept"]


def api_for(client, repo) -> KeelApi:
    """keel's api as keel2 mcp reaches it, cut down: /api/projects, and /api/org/* passed on to the engine's /org/*."""
    def handle(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/api/projects":
            return httpx.Response(200, json=[{"id": "demo", "name": "demo", "root": str(repo)}])
        if path.startswith("/api/org/"):
            r = client.request(req.method, path[len("/api"):], params=dict(req.url.params), content=req.content,
                               headers={"content-type": "application/json"})
            return httpx.Response(r.status_code, content=r.content, headers={"content-type": "application/json"})
        return httpx.Response(404, json={"error": f"No route {path}"})
    return KeelApi("http://keel.test", transport=httpx.MockTransport(handle))


def test_its_tools_are_in_keels_mcp_server_at_their_levels():
    names = lambda level: {t.name for t in asyncio.run(mcp_server.build_server(level=level, api=object()).list_tools())}
    assert set(READ) <= names("read") and not set(WRITE) & names("read")
    assert set(READ + WRITE) <= names("write")
    assert all(mcp_levels.tool_level(n) == "read" for n in READ)
    assert all(mcp_levels.tool_level(n) == "write" for n in WRITE)


def test_a_client_makes_an_org_gives_it_a_mission_and_follows_it(client, repo):
    api = api_for(client, repo)
    assert org_mcp.org_list(api).startswith("No org yet.")
    tpl = org_mcp.org_templates(api)
    assert "- solo: Solo developer (1 person(s), 2 agent(s), 2 levels)" in tpl and "- startup: Startup" in tpl
    assert org_mcp.org_save(api, "solo", template="solo").startswith("Saved org solo (Solo developer): roles ")
    with pytest.raises(ApiError):
        org_mcp.org_save(api, "empty")
    assert org_mcp.org_save(api, "shop", yaml=ORG).startswith("Saved org shop")
    listed = org_mcp.org_list(api)
    assert "2 org(s):" in listed and "- shop: " in listed and "agents: lead, fe, be" in listed
    shown = org_mcp.org_show(api, "shop", yaml=True)
    assert "- lead: Tech lead (agent · act) · runs lead-intake" in shown and "team: fe, be" in shown
    assert "- me → lead: delegate" in shown and "```yaml" in shown and "Works on: demo" in shown

    text = org_mcp.org_give_mission(api, "shop", "lead", "Cart count in the header", "Show the count next to the cart.",
                                    project="demo", cap_tokens=400_000)
    mid = text.split()[1]
    assert text.startswith(f"Mission {mid} given to lead in shop on demo")
    sent = wait_for(lambda: [s for s in client.started if s["workflow_id"] == "lead-intake"], what="the lead's flow")
    assert sent[0]["title"] == "Tech lead: Cart count in the header"
    # the lead's review waits at the report gate: the tools say so and name the thread to answer
    wait_for(lambda: "WAITS for the person at accept" in org_mcp.org_mission(api, mid), what="the report gate")
    tree = org_mcp.org_mission(api, mid)
    assert f"Mission {mid} in org shop: Cart count in the header (running)" in tree
    assert "  - fe: " in tree and "  - be: " in tree and "keel_" not in tree.split("WAITS", 1)[0]
    listed = org_mcp.org_missions(api, "shop")
    assert f"- {mid}: Cart count in the header · to lead · running" in listed and "keel_resume" in listed
    assert "WAITS for you at accept" in listed                      # the mission you gave waits for you, not a boss
    assert org_mcp.org_stop_mission(api, mid) == f"Mission {mid} is stopped: it and everything under it stopped."
    wait_status(client, mid, "stopped")


def test_refusals_come_back_as_keels_words(client, repo):
    api = api_for(client, repo)
    org_mcp.org_save(api, "shop", yaml=ORG)
    with pytest.raises(ApiError, match="me is a person"):
        org_mcp.org_give_mission(api, "shop", "me", "x", project="demo")
    with pytest.raises(ApiError, match="No org called nope"):
        org_mcp.org_show(api, "nope")
    with pytest.raises(ApiError, match="Only a reported mission can be accepted"):
        mid = org_mcp.org_give_mission(api, "shop", "lead", "x", project="demo").split()[1]
        org_mcp.org_accept(api, mid)
