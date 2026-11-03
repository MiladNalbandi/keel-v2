"""What library_search and library_read answer. The api holds the documents and decides what an agent may see: this module only
asks it, for the project of the agent call (never one the agent names)."""

from __future__ import annotations

import os
from urllib.parse import quote

import httpx

from keel_engine import config


def _get(path: str, params: dict | None = None) -> httpx.Response:
    base = os.environ.get("KEEL_API_URL", "http://127.0.0.1:8080").rstrip("/")
    return httpx.get(base + path, params=params, headers={"X-Keel-Token": config.internal_token()}, timeout=20)


def _trouble(r: httpx.Response) -> str:
    if r.status_code == 404:
        return "The library has no such document for this project."
    return f"The library did not answer (status {r.status_code})."


def search(project: str, query: str, limit: int = 5) -> str:
    query = (query or "").strip()
    if not query:
        return "Say what to look for."
    try:
        r = _get(f"/api/projects/{quote(project, safe='')}/library/agent/search", {"q": query, "limit": min(max(int(limit or 5), 1), 10)})
    except httpx.HTTPError:
        return "The library cannot be reached now."
    if r.status_code != 200:
        return _trouble(r)
    hits = r.json()
    if not hits:
        return f'The library says nothing about "{query}". Do not fill the gap: say that it says nothing.'
    out = []
    for h in hits:
        where = f"{h['title']}" + (f" › {h['section']}" if h.get("section") else "") + f" (version {h['version']})"
        out.append(f"library:{h['doc_id']} {where}\n> " + h["quote"].replace("\n", "\n> "))
    return "\n\n".join(out) + "\n\nQuote the words you use and name the source as library:<id>."


def read(project: str, doc_id: str) -> str:
    doc_id = (doc_id or "").removeprefix("library:").strip()
    if not doc_id:
        return "Say which document: its id, as library_search gives it."
    try:
        r = _get(f"/api/projects/{quote(project, safe='')}/library/agent/docs/{quote(doc_id, safe='')}")
    except httpx.HTTPError:
        return "The library cannot be reached now."
    if r.status_code != 200:
        return _trouble(r)
    d = r.json()
    return f"library:{d['id']} {d['title']} (version {d['version']})\n\n{d['body']}"


def call_tool(call: dict, tool: str, args: dict) -> str:
    project = str(call.get("project") or "")
    if tool == "library_search":
        return search(project, str(args.get("query") or ""), args.get("limit") or 5)
    if tool == "library_read":
        return read(project, str(args.get("doc_id") or ""))
    return f"The library has no tool called {tool}."
