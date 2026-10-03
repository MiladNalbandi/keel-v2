"""POST /agents/ask: one question to a model, used by the api's Doctor."""
import os
import stat

from keel_engine.models import cli as cli_mod


def test_fake_model_answers_fake_so_the_caller_uses_its_rules(client):
    r = client.post("/agents/ask", json={"model": {"provider": "fake", "model": "fake"}, "prompt": "hi"})
    assert r.status_code == 200
    assert r.json()["ok"] is True and r.json()["fake"] is True


def test_claude_subscription_gets_the_login_and_the_prompt(client, tmp_path, monkeypatch):
    monkeypatch.setenv("KEEL_FAKE", "0")
    # a stand-in claude CLI that prints the stream-json "result" line, and records what it got
    seen = tmp_path / "seen.txt"
    stub = tmp_path / "claude"
    stub.write_text("#!/bin/sh\n"
                    f'echo "TOKEN=$CLAUDE_CODE_OAUTH_TOKEN" > {seen}\n'
                    f'cat >> {seen}\n'
                    'echo \'{"type":"result","subtype":"success","result":"{\\"summary\\":\\"ok\\"}","usage":{"input_tokens":12,"output_tokens":5}}\'\n')
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("KEEL_CLAUDE_BIN", str(stub))
    r = client.post("/agents/ask", json={"model": {"provider": "claude", "mode": "subscription", "model": "sonnet"},
                                         "system": "You are the doctor.", "prompt": "Look at these files.",
                                         "keys": {"claude_oauth": "tok-123"}})
    body = r.json()
    assert body["ok"], body
    assert '"summary"' in body["text"]
    text = seen.read_text()
    assert "TOKEN=tok-123" in text and "Look at these files." in text


def test_a_missing_cli_is_a_clear_error(client, monkeypatch):
    monkeypatch.setenv("KEEL_FAKE", "0")
    monkeypatch.setenv("KEEL_CODEX_BIN", "/nope/codex")
    monkeypatch.setattr(cli_mod.shutil, "which", lambda *_: None)
    r = client.post("/agents/ask", json={"model": {"provider": "codex", "mode": "subscription", "model": "gpt-5"}, "prompt": "x"})
    assert r.json()["ok"] is False
