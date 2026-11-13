"""POST /agents/ask: one question to a model, used by the api's Doctor."""
from scripted_model import scripted


def test_fake_model_answers_fake_so_the_caller_uses_its_rules(client):
    r = client.post("/agents/ask", json={"model": {"provider": "fake", "model": "fake"}, "prompt": "hi"})
    assert r.status_code == 200
    assert r.json()["ok"] is True and r.json()["fake"] is True


def test_a_claude_model_asks_through_the_api_with_its_key(client, monkeypatch):
    # #101 no CLI and no login: one question goes to the model's API with keel's API key, whatever mode was saved
    sm = scripted(monkeypatch, '{"summary": "ok"}')
    r = client.post("/agents/ask", json={"model": {"provider": "claude", "mode": "subscription", "model": "sonnet"},
                                         "system": "You are the doctor.", "prompt": "Look at these files.",
                                         "keys": {"claude": "sk-ant-test", "claude_oauth": "tok-123"}})
    body = r.json()
    assert body["ok"], body
    assert '"summary"' in body["text"] and body["tokens_in"] == 10
    [call] = sm.calls
    assert call.key == "sk-ant-test" and call.model["mode"] == "api"
    assert call.req.system == "You are the doctor." and call.last_user_text() == "Look at these files." and not call.req.tools


def test_without_a_key_the_answer_says_where_to_add_one(client, monkeypatch):
    monkeypatch.setenv("KEEL_FAKE", "0")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = client.post("/agents/ask", json={"model": {"provider": "claude", "mode": "api", "model": "sonnet"}, "prompt": "x"}).json()
    assert r["ok"] is False and "No Anthropic API key" in r["error"] and "Settings" in r["error"]
