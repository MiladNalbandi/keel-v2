"""The pieces every keel MCP module shares: the api client (KeelApi), its error, the project / flow lookups and a few
text helpers. This module imports nothing from the other mcp_* modules, so they can all import it."""
from __future__ import annotations

import copy
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, unquote

import httpx

API_TIMEOUT = 15


MCP_TOKEN_HEADER = "X-Keel-Mcp-Token"


class ApiError(Exception):
    def __init__(self, message: str, status: int | None = None, body: dict | None = None):
        super().__init__(message)
        self.status = status     # the api's HTTP status, when it answered
        self.body = body or {}   # 0.18.57 the error's other fields (a refused start: ignorable paths, owners)


SCOPE_ENV = "KEEL_MCP_PROJECTS"


def scope() -> list[str] | None:
    """0.18.64 the projects this server answers for (KEEL_MCP_PROJECTS, set by the engine for a flow's agents: the
    flow's own project first); None: every project (a person's own MCP client)."""
    raw = os.environ.get(SCOPE_ENV, "").strip()
    return [p for p in (x.strip() for x in raw.split(",")) if p] if raw else None


def out_of_scope(pid: str, sc: list[str]) -> str:
    return (f"{pid} is another project: keel's MCP server for this flow's agents reads only {', '.join(sc)} (the "
            "rule other-projects decides which other projects a flow may read).")


def internal_token() -> str:
    """KEEL_INTERNAL_TOKEN, else the file KEEL_INTERNAL_TOKEN_FILE names (keel2 mcp in Docker: docker/keel-start writes it)."""
    tok = os.environ.get("KEEL_INTERNAL_TOKEN", "").strip()
    path = os.environ.get("KEEL_INTERNAL_TOKEN_FILE", "")
    if not tok and path:
        try:
            with open(path, encoding="utf-8") as f:
                tok = f.read().strip()
        except OSError:
            tok = ""
    return tok


class KeelApi:
    """The keel v2 api over HTTP (KEEL_API_URL). Public /api routes; the api only listens on 127.0.0.1."""

    def __init__(self, url: str | None = None, transport: httpx.BaseTransport | None = None):
        self.url = (url or os.environ.get("KEEL_API_URL") or "http://127.0.0.1:8080").rstrip("/")
        self.http = httpx.Client(base_url=self.url + "/api", timeout=API_TIMEOUT, transport=transport)
        # v0.16.1 the api checks the level: with the internal token (keel2 mcp has it, agents never do) this asks the api
        # for its level's token once and sends it as X-Keel-Mcp-Token; without it, no token, so the api treats it as read.
        # A dict, so the copies acting() makes share it.
        # v0.18.1 without the internal token, the token the engine gave this server (KEEL_MCP_TOKEN: keel's agents' own
        # server, read level), since the api answers no caller without one.
        self._proof = {"internal": internal_token(), "level": "read", "asked": False,
                       "given": os.environ.get("KEEL_MCP_TOKEN", "").strip()}
        # v0.16.0 who acts: every write the api gets from here says "via MCP · <client>: <tool>" (api McpAudit)
        self.client = "an MCP client"
        self.tool = ""

    def acting(self, client: str, tool: str) -> "KeelApi":
        """A copy for one tool call (the same HTTP client) that says who acts: two calls at once never swap names."""
        c = copy.copy(self)
        c.client, c.tool = client, tool
        return c

    def use_level(self, level: str) -> None:
        """The level to ask the api's token for (build_server sets it); asked on the first request."""
        self._proof.update(level=level, asked=False)
        self.http.headers.pop(MCP_TOKEN_HEADER, None)

    def _prove(self) -> None:
        p = self._proof
        if p["asked"]:
            return
        if not p["internal"]:
            if p["given"]:
                self.http.headers[MCP_TOKEN_HEADER] = p["given"]
                p["asked"] = True
            return
        try:
            res = self.http.post("/mcp/token", json={"level": p["level"]}, headers={"X-Keel-Token": p["internal"]})
        except httpx.HTTPError:
            return                                          # the api is not up yet: ask again on the next request
        if res.status_code != 200:
            return                                          # the api is starting or said no: ask again on the next request
        p["asked"] = True
        try:
            token = res.json().get("token") if res.status_code == 200 else None
        except ValueError:
            token = None
        if token:
            self.http.headers[MCP_TOKEN_HEADER] = token

    def name_client(self, name: str | None) -> None:
        """0.18.66 the client's own name (its MCP initialize), once: every request says it, reads too, so keel's Help ›
        Start here and Settings › Access see which AI tool is connected (api McpClients). Shared by the copies."""
        if name and not self._proof.get("client"):
            self._proof["client"] = str(name)[:80]

    def _audit(self, method: str) -> dict:
        # 0.18.66 keel's own agents' server (the engine gave it its token; it has no internal token) says so: the api
        # does not count it as a person's AI tool
        mine = {"X-Keel-Mcp-Kind": "agent"} if not self._proof["internal"] and self._proof["given"] else {}
        if method == "GET":
            known = self._proof.get("client")
            return {**mine, **({"X-Keel-Client": known} if known else {})}
        client = self.client if self.client != "an MCP client" else (self._proof.get("client") or self.client)
        return {**mine, "X-Keel-Via": "mcp", "X-Keel-Client": client[:80], "X-Keel-Tool": self.tool[:80]}

    def _scope(self, method: str, path: str, kw: dict) -> None:
        """0.18.64 a flow's agents' server (KEEL_MCP_PROJECTS, tools/mcp.py scoped) answers only for its projects: a
        project's routes, and a flow or an agent call of one of them; the Inbox and the job list of the first one."""
        sc = scope()
        if sc is None:
            return
        m = re.match(r"^/projects/([^/?]+)", path)
        if m:
            if unquote(m.group(1)) not in sc:
                raise ApiError(out_of_scope(unquote(m.group(1)), sc), 403)
            return
        params = dict(kw.get("params") or {})
        if path in ("/inbox", "/jobs", "/jobs/count"):
            if params.get("project") and params["project"] not in sc:
                raise ApiError(out_of_scope(str(params["project"]), sc), 403)
            params.setdefault("project", sc[0])
            if not params["project"]:
                params["project"] = sc[0]
            kw["params"] = params
            return
        m = re.match(r"^/jobs/([^/?]+)", path)
        if m:
            job = self._raw("GET", f"/jobs/{m.group(1)}", _scoped=False)
            pid = (job.json() if job.content else {}).get("project_id")
            if pid not in sc:
                raise ApiError(out_of_scope(str(pid or "another project"), sc), 403)
            return
        m = re.match(r"^/threads/([^/?]+)", path)
        if m:
            for pid in sc:
                try:
                    self._raw("GET", f"/projects/{q(pid)}/flows/{m.group(1)}", _scoped=False)
                    return
                except ApiError:
                    continue
            raise ApiError(f"flow {unquote(m.group(1))} is not one of {', '.join(sc)}: this server reads only "
                           f"{'that project' if len(sc) == 1 else 'those projects'}.", 403)
        if path.startswith(("/folders", "/inbox/")):
            raise ApiError("this server reads only its own project's files and flows (keel's MCP server for a flow's agents).", 403)

    def _raw(self, method: str, path: str, timeout: float | None = None, _scoped: bool = True, **kw) -> httpx.Response:
        if _scoped:
            self._scope(method, path, kw)
        self._prove()
        try:
            res = self.http.request(method, path, headers=self._audit(method), **({"timeout": timeout} if timeout else {}), **kw)
        except httpx.HTTPError as exc:
            # 0.18.64 a keel restart (keel2 add, keel2 update) is the usual reason: the same call works again in a moment
            raise ApiError(f"keel's api does not answer at {self.url} ({type(exc).__name__}): keel is restarting or "
                           "stopped. Try again in a few seconds (keel2 status).") from exc
        if res.status_code >= 400:
            body = None
            try:
                body = res.json()
                msg = body.get("error") or res.text
                if body.get("hint"):
                    msg += f" ({body['hint']})"
            except (ValueError, AttributeError):
                msg = res.text[:300]
            raise ApiError(f"{method} {path}: {res.status_code} {msg}", res.status_code, body if isinstance(body, dict) else None)
        return res

    def _send(self, method: str, path: str, **kw) -> Any:
        res = self._raw(method, path, **kw)
        return res.json() if res.content else None

    def text(self, route: str, /, **params) -> str:
        return self._raw("GET", route, params={k: v for k, v in params.items() if v is not None}).text

    def put(self, path: str, body: dict) -> Any:
        return self._send("PUT", path, json=body)

    def patch(self, path: str, body: dict) -> Any:
        return self._send("PATCH", path, json=body)

    def delete(self, path: str) -> Any:
        return self._send("DELETE", path)

    def get(self, route: str, /, **params) -> Any:
        """GET a route; `params` are the query (a `path` query parameter is fine: the route is positional)."""
        got = self._send("GET", route, params={k: v for k, v in params.items() if v is not None})
        sc = scope()
        if sc is not None and route == "/projects" and isinstance(got, list):
            got = [p for p in got if isinstance(p, dict) and p.get("id") in sc]     # 0.18.64 the others stay unknown
        return got

    def post(self, path: str, body: dict, timeout: float | None = None) -> Any:
        return self._send("POST", path, json=body, timeout=timeout)


# ---------------------------------------------------------------- helpers

def client_folder() -> str | None:
    """The folder the MCP client works in, as keel sees it. 0.18.64 `keel2 mcp` passes it (KEEL_CLIENT_CWD; empty when
    keel cannot see that folder): the server runs in keel's container, whose own folder is /workspace (the main
    project), so a tool without a project answered for the main project from any folder. Without the variable (an
    agent's server, native keel) the server's own folder is the client's."""
    if "KEEL_CLIENT_CWD" in os.environ:
        cwd = os.environ["KEEL_CLIENT_CWD"].strip()
        return os.path.normpath(cwd) if cwd else None
    return os.path.realpath(os.getcwd())


def _project(api: KeelApi, project: str | None) -> dict:
    """The project a tool is for: the one named (or KEEL_PROJECT), else the only one, else the one that holds the
    client's folder. 0.18.64 else none: the answer names the projects to pick from. keel_flows without a project
    answered for the first project, an unrelated one (a write refused already; now a read does too)."""
    projects = api.get("/projects") or []
    want = project or os.environ.get("KEEL_PROJECT") or ""
    if want:
        for p in projects:
            if want in (p.get("id"), p.get("name")):
                return p
        raise ApiError(f'no project "{want}". keel knows: {", ".join(p["id"] for p in projects) or "none yet"}')
    if not projects:
        raise ApiError("keel has no project yet. Add one in the dashboard (Projects › Add).")
    if len(projects) == 1:
        return projects[0]
    cwd = client_folder()
    inside = [p for p in projects if cwd and p.get("root") and (cwd == p["root"] or cwd.startswith(p["root"].rstrip("/") + "/"))]
    if inside:
        return max(inside, key=lambda p: len(p["root"]))
    where = "this folder is in none of keel's projects" if cwd else "keel cannot see the folder you work in"
    raise ApiError(f"{where}, so keel does not guess which project you mean. Pass project (one of: "
                   f'{", ".join(p["id"] for p in projects)}).')


def _flow(api: KeelApi, pid: str) -> tuple[dict | None, dict | None]:
    view = api.get(f"/projects/{pid}/flow") or {}
    return view.get("thread"), view.get("workflow")


def _waits(thread: dict | None) -> bool:
    return bool(thread and thread.get("status") == "waiting" and thread.get("waiting"))


def _flow_of(api: KeelApi, pid: str, tid: str) -> dict | None:
    """One flow of the project by its thread id (GET /projects/{pid}/flows/{tid}); None when the project has no such
    flow. The api has no GET /threads/{id}: that is the engine's route."""
    try:
        return (api.get(f"/projects/{q(pid)}/flows/{q(tid)}") or {}).get("thread")
    except ApiError as exc:
        if exc.status == 404:
            return None
        raise


def _inbox(api: KeelApi, pid: str | None = None) -> list[dict]:
    """The Inbox's items (of one project); none when the api cannot list them (the lookups then work as before)."""
    try:
        return (api.get("/inbox", project=pid) or {}).get("items") or []
    except ApiError:
        return []


def waiting_thread(api: KeelApi, project: str | None = None, thread_id: str | None = None) -> tuple[dict | None, str]:
    """(the waiting flow an answer is for, "") or (None, why there is none; "" when nothing waits at all).

    The project's Flow page shows one flow (its folder's newest), so a worktree flow, or a flow started after another,
    waits where /projects/{pid}/flow does not look. thread_id: that flow, in the project the Inbox lists it under (else
    the project as the tool finds it). No thread_id: the project's current flow when it waits, else the one flow of the
    project the Inbox lists as waiting; several: none is picked, they are named."""
    if thread_id:
        items = _inbox(api)
        pid = next((i["project_id"] for i in items if i.get("thread_id") == thread_id and i.get("project_id")), None)
        pid = pid or _project(api, project)["id"]
        thread = _flow_of(api, pid, thread_id)
        if thread is None:
            return None, f"no flow {thread_id} on {pid}."
        return (thread, "") if _waits(thread) else (None, f"thread {thread_id} waits for nothing.")
    pid = _project(api, project)["id"]
    thread, _ = _flow(api, pid)
    if _waits(thread):
        return thread, ""
    items = _inbox(api, pid)
    tids = list(dict.fromkeys(i["thread_id"] for i in items if i.get("thread_id") and i.get("project_id") in (None, pid)
                              and not (i.get("task") or i.get("permission") or i.get("payload"))))
    found = [t for t in (_flow_of(api, pid, tid) for tid in tids) if _waits(t)]
    if len(found) == 1:
        return found[0], ""
    if found:
        return None, (f"{len(found)} flows wait on {pid}; pass thread_id for the one to answer:\n" + "\n".join(
            f"- {t.get('thread_id')}  \"{t.get('title')}\": {(t.get('waiting') or {}).get('title')}" for t in found))
    return None, ""


def _n(x: Any) -> str:
    try:
        return f"{int(x or 0):,}"
    except (TypeError, ValueError):
        return str(x)


def _when(at: str | None) -> datetime:
    """A keel time (ISO, UTC) as a naive UTC datetime for sorting; datetime.min when it is not a time."""
    try:
        d = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min
    return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d


def _clock(at: str | None) -> str:
    """0.18.64 a keel time in the person's time zone, with the zone: "10-05 12:04:00 CEST". keel stores UTC while files
    and shells show local time; `keel2 mcp` passes the computer's zone (TZ) into keel's container."""
    d = _when(at)
    if d == datetime.min:
        return "--"
    local = d.replace(tzinfo=timezone.utc).astimezone()
    return local.strftime("%m-%d %H:%M:%S ") + zone_name(local)


def zone_name(d: datetime | None = None) -> str:
    """The local zone's short name (CET, EDT), else its offset (UTC+05:30); UTC when there is no offset."""
    d = d or datetime.now(timezone.utc).astimezone()
    name = d.tzname() or ""
    if name[:1].isalpha():
        return name
    off = d.utcoffset() or timedelta(0)
    if not off:
        return "UTC"
    m = int(off.total_seconds()) // 60
    return f"UTC{'+' if m >= 0 else '-'}{abs(m) // 60:02d}:{abs(m) % 60:02d}"


def cut(text: str, n: int) -> str:
    """At most n characters, cut at a word boundary, with "…" when something was left out."""
    text = str(text or "")
    if len(text) <= n:
        return text
    head = text[:n]
    at = max(head.rfind(" "), head.rfind("\n"))
    return (head[:at] if at > n // 2 else head).rstrip() + " …"


def q(x: str) -> str:
    """URL-encode one path segment."""
    return quote(str(x), safe="")
