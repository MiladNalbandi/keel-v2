"""What the Links tools answer. The key comes with the agent call (keys links:linear:token and links:linear:url, the api's LinksKeys). A
tool that writes shows the person the exact words in keel's Inbox and acts only after their yes, in every run mode."""

from __future__ import annotations

from .linear import Linear, LinearError

TEXT_MAX = 20_000
TITLE_MAX = 200
NOT_LINKED = "Linear is not linked yet: the person adds a key in Settings › Connections › Linear."


def _client(call: dict) -> Linear | None:
    keys = call.get("keys") or {}
    token = keys.get("links:linear:token")
    return Linear(keys.get("links:linear:url") or "https://api.linear.app/graphql", token) if token else None


def _ask(call: dict, title: str, words: str) -> tuple[bool, str]:
    """Asks the person in keel's Inbox and waits: (yes, why). Without a way to ask, the answer is no."""
    bus = call.get("bus")
    if bus is None:
        return False, "keel cannot ask the person from here."
    from keel_engine import approvals

    broker = approvals.of(bus)
    q = broker.open("plugin", call.get("project") or "", title, words, source="plugin:links", thread_id=call.get("thread"), session=call.get("session"))
    ans = broker.wait_blocking(q["id"])
    return ans.get("decision") == "allow", ans.get("why") or ""


def _state(issue: dict) -> str:
    return (issue.get("state") or {}).get("name") or "?"


def search(call: dict, term: str, limit: int = 10) -> str:
    client = _client(call)
    if client is None:
        return NOT_LINKED
    term = (term or "").strip()
    if not term:
        return "Say what to look for."
    nodes = client.search(term, min(max(int(limit or 10), 1), 25))
    if not nodes:
        return f'Linear has no issue for "{term}".'
    return "\n".join(f"{n.get('identifier')} [{_state(n)}] {n.get('title')} {n.get('url') or ''}".rstrip() for n in nodes)


def issue(call: dict, key: str) -> str:
    client = _client(call)
    if client is None:
        return NOT_LINKED
    i = client.issue((key or "").strip())
    if not i:
        return f"Linear has no issue {key}."
    out = [f"{i.get('identifier')} [{_state(i)}] {i.get('title')}", str(i.get("url") or ""), "", str(i.get("description") or "(no description)")]
    comments = ((i.get("comments") or {}).get("nodes") or [])
    if comments:
        out += ["", f"Comments ({len(comments)}):"] + [f"- {c.get('body')}" for c in comments]
    return "\n".join(out)


def comment(call: dict, key: str, text: str) -> str:
    client = _client(call)
    if client is None:
        return NOT_LINKED
    key, text = (key or "").strip(), (text or "").strip()
    if not key or not text:
        return "Say which issue and what the comment says."
    if len(text) > TEXT_MAX:
        return f"The comment is too long ({TEXT_MAX} characters at most)."
    i = client.issue(key)
    if not i:
        return f"Linear has no issue {key}."
    yes, why = _ask(call, f"May keel comment on {i.get('identifier')} in Linear?", f"Comment on {i.get('identifier')}: {i.get('title')}\n\n{text}")
    if not yes:
        return f"Nothing was written: the person said no. {why}".strip()
    url = client.comment(i["id"], text)
    return f"The comment is on {i.get('identifier')}. {url}".strip()


def create_issue(call: dict, team: str, title: str, text: str = "") -> str:
    client = _client(call)
    if client is None:
        return NOT_LINKED
    team, title, text = (team or "").strip(), (title or "").strip(), (text or "").strip()
    if not team or not title:
        return "Say which team (its key, like ENG) and the issue's title."
    if len(title) > TITLE_MAX or len(text) > TEXT_MAX:
        return f"Too long: a title has {TITLE_MAX} characters at most, a text {TEXT_MAX}."
    team_id = client.team_id(team)
    if not team_id:
        return f"Linear has no team with the key {team}."
    yes, why = _ask(call, f"May keel make a new issue in Linear ({team})?", f"New issue in {team}: {title}\n\n{text}")
    if not yes:
        return f"Nothing was written: the person said no. {why}".strip()
    made, url = client.create_issue(team_id, title, text)
    return f"The issue {made} is made. {url}".strip()


def call_tool(call: dict, tool: str, args: dict) -> str:
    try:
        if tool == "links_search":
            return search(call, str(args.get("query") or ""), args.get("limit") or 10)
        if tool == "links_issue":
            return issue(call, str(args.get("key") or ""))
        if tool == "links_comment":
            return comment(call, str(args.get("key") or ""), str(args.get("text") or ""))
        if tool == "links_create_issue":
            return create_issue(call, str(args.get("team") or ""), str(args.get("title") or ""), str(args.get("text") or ""))
    except LinearError as exc:
        return str(exc)
    return f"Links has no tool called {tool}."
