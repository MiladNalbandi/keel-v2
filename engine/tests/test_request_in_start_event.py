"""A flow's request is kept in its thread.started event (cut to a limit), so a part can read what the person asked for after the flow's
start (the Record plugin). A flow with no request adds nothing."""

from conftest import start, wait
from keel_engine.runtime import service
from keel_engine.workflows.model import from_dict

WF = from_dict({"name": "custom", "keel_rules": False, "steps": [{"id": "g", "kind": "gate", "name": "g"}]})


def test_the_start_event_holds_the_request_cut_to_its_limit(client, repo, monkeypatch):
    seen = []
    real = client.bus.emit

    def emit(kind, thread=None, project=None, *a, **k):
        if kind == "thread.started":
            seen.append(k.get("data"))
        return real(kind, thread, project, *a, **k)
    monkeypatch.setattr(client.bus, "emit", emit)
    wait(client, start(client, repo, workflow=WF, request="Let people export invoices"))
    assert seen[-1]["request"] == "Let people export invoices"
    wait(client, start(client, repo, workflow=WF, request="x" * (service.REQUEST_RECORD_MAX + 5)))
    assert len(seen[-1]["request"]) == service.REQUEST_RECORD_MAX
    wait(client, start(client, repo, workflow=WF))
    assert "request" not in seen[-1]


def test_a_start_from_slack_names_who_only_then(client, repo, monkeypatch):
    """0.21.0 data.started_via and data.started_by reach thread.started; `by` only with via slack, and only with its prefix."""
    seen = []
    real = client.bus.emit

    def emit(kind, thread=None, project=None, *a, **k):
        if kind == "thread.started":
            seen.append(k.get("data"))
        return real(kind, thread, project, *a, **k)
    monkeypatch.setattr(client.bus, "emit", emit)
    wait(client, start(client, repo, workflow=WF, data={"started_via": "slack", "started_by": "slack:Ann (U1)"}))
    assert seen[-1]["via"] == "slack" and seen[-1]["by"] == "slack:Ann (U1)"
    wait(client, start(client, repo, workflow=WF, data={"started_via": "mcp", "started_by": "slack:Ann (U1)"}))
    assert seen[-1]["via"] == "mcp" and "by" not in seen[-1]
    wait(client, start(client, repo, workflow=WF, data={"started_via": "slack", "started_by": "supervisor"}))
    assert "by" not in seen[-1]
