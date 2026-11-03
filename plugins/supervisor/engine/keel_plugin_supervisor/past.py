"""The person's earlier answers to the same kind of gate, in other flows of the project: one read call to keel's api
(GET /api/projects/{pid}/supervisor/past-answers). The brief shows them as data. Without them the look goes on: any
failure here (the api is down, slow or says no) is an empty list, never a failed look.
"""

from __future__ import annotations

from urllib.parse import quote

import httpx

from keel_engine import config

TIMEOUT = 5.0
LIMIT = 10


def fetch(project_id: str, kind: str, step: str | None, thread_id: str) -> list[dict]:
    url = config.api_url()
    if not url.startswith("http"):       # KEEL_API_URL=off: no api to ask
        return []
    try:
        r = httpx.get(f"{url}/api/projects/{quote(project_id, safe='')}/supervisor/past-answers",
                      params={"kind": kind, "step": step or "", "notThread": thread_id, "limit": LIMIT},
                      headers={"X-Keel-Token": config.internal_token()}, timeout=TIMEOUT)
        r.raise_for_status()
        rows = r.json().get("answers")
    except (httpx.HTTPError, ValueError, AttributeError):
        return []
    return [a for a in rows if isinstance(a, dict)][:LIMIT] if isinstance(rows, list) else []
