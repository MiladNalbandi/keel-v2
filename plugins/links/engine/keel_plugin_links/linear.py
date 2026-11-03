"""Linear's GraphQL API, the little of it these tools use. No names of people: an agent does not need them, so they never go to the
model company through here."""

from __future__ import annotations

import httpx

TIMEOUT = 20


class LinearError(Exception):
    """A failure in plain words. It never holds the key."""


class Linear:
    def __init__(self, url: str, token: str):
        self.url = url
        self.token = token

    def _ask(self, query: str, variables: dict) -> dict:
        try:
            r = httpx.post(self.url, json={"query": query, "variables": variables},
                           headers={"Authorization": self.token, "Content-Type": "application/json"}, timeout=TIMEOUT, follow_redirects=False)
        except httpx.HTTPError as exc:
            raise LinearError(f"Linear cannot be reached ({type(exc).__name__}).") from None
        if r.status_code in (401, 403):
            raise LinearError("Linear refused the key: check it in Settings › Connections › Linear.")
        try:
            body = r.json()
        except ValueError:
            raise LinearError(f"Linear answered {r.status_code}.") from None
        if body.get("errors"):
            raise LinearError("Linear said: " + str(body["errors"][0].get("message"))[:200])
        if r.status_code != 200 or not isinstance(body.get("data"), dict):
            raise LinearError(f"Linear answered {r.status_code}.")
        return body["data"]

    def search(self, term: str, limit: int) -> list[dict]:
        d = self._ask("query($term: String!, $first: Int) { searchIssues(term: $term, first: $first) { nodes { identifier title url state { name } } } }",
                      {"term": term, "first": limit})
        return (d.get("searchIssues") or {}).get("nodes") or []

    def issue(self, key: str) -> dict | None:
        d = self._ask("query($id: String!) { issue(id: $id) { id identifier title description url state { name } comments(first: 20) { nodes { body createdAt } } } }",
                      {"id": key})
        return d.get("issue")

    def comment(self, issue_id: str, text: str) -> str:
        d = self._ask("mutation($input: CommentCreateInput!) { commentCreate(input: $input) { success comment { url } } }",
                      {"input": {"issueId": issue_id, "body": text}})
        made = d.get("commentCreate") or {}
        if not made.get("success"):
            raise LinearError("Linear did not take the comment.")
        return str((made.get("comment") or {}).get("url") or "")

    def team_id(self, key: str) -> str | None:
        d = self._ask("query($key: String!) { teams(filter: { key: { eq: $key } }) { nodes { id } } }", {"key": key})
        nodes = (d.get("teams") or {}).get("nodes") or []
        return nodes[0]["id"] if nodes else None

    def create_issue(self, team_id: str, title: str, text: str) -> tuple[str, str]:
        d = self._ask("mutation($input: IssueCreateInput!) { issueCreate(input: $input) { success issue { identifier url } } }",
                      {"input": {"teamId": team_id, "title": title, "description": text}})
        made = d.get("issueCreate") or {}
        if not made.get("success"):
            raise LinearError("Linear did not take the new issue.")
        issue = made.get("issue") or {}
        return str(issue.get("identifier") or ""), str(issue.get("url") or "")
