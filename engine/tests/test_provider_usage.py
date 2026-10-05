"""The usage dashboard's engine side: plan windows from claude runs, codex app-server and GitHub, and the pause rule."""
import json
import sys
import time
from pathlib import Path

import httpx

from conftest import decide, start, wait
from keel_engine.models import usage
from keel_engine.models.cli_runners import ClaudeCLIRunner, ClaudeStream
from test_agent_memory import _claude, _req
from test_v02 import script

FIX = Path(__file__).parent / "fixtures" / "usage"
CLAUDE = {"default": {"provider": "claude", "mode": "subscription", "model": "haiku"}}
RESULT = {"type": "result", "result": "- **AC-1** [API] A player's rank is shown next to their best score",
          "usage": {"input_tokens": 10, "output_tokens": 2}}


def _lines():
    return (FIX / "claude_rate_limit_events.jsonl").read_text().splitlines()


def _info(n):
    return json.loads(_lines()[n]).get("rate_limit_info")


# ------------------------------------------------------------------ claude: rate_limit_event shapes

def test_unified_windows_from_a_recorded_claude_line():
    got = {w["window"]: w for w in usage.claude_windows(_info(0))}
    assert got["five_hour"]["used_pct"] == 0.2 and got["five_hour"]["resets_at"] == 1791229200
    assert got["five_hour"]["status"] == "allowed" and got["five_hour"]["label"] == "5-hour"
    assert got["seven_day"]["used_pct"] == 0.05 and got["seven_day"]["label"] == "weekly"


def test_single_window_warning_and_refusal_shapes():
    [w] = usage.claude_windows(_info(1))
    assert (w["window"], w["used_pct"], w["status"]) == ("five_hour", 0.83, "allowed_warning")
    [r] = usage.claude_windows(_info(2))
    assert (r["window"], r["used_pct"], r["status"], r["resets_at"]) == ("seven_day", 1.0, "rejected", 1791460800)


def test_garbage_never_crashes_and_gives_nothing_it_cannot_read():
    assert usage.claude_windows(_info(3)) == []
    assert usage.claude_windows(_info(4)) == []
    assert usage.claude_windows(_info(5)) == []
    [w] = usage.claude_windows(_info(6))           # a window with unreadable numbers is kept without them
    assert w["window"] == "seven_day" and w["used_pct"] is None and w["resets_at"] is None
    s = ClaudeStream(lambda *a, **k: None, "/tmp")
    for line in _lines():
        s.line(line)
    assert {w["window"] for w in s.windows} == {"five_hour", "seven_day"}
    assert usage.LATEST["claude"]["source"] == "last run"


async def test_a_claude_run_returns_its_windows(tmp_path, monkeypatch, repo):
    monkeypatch.setenv("KEEL_CLAUDE_BIN", _claude(tmp_path, [json.loads(_lines()[0]), RESULT]))
    res = await ClaudeCLIRunner().run(_req(repo), lambda *a, **k: None)
    assert {w["window"] for w in res.data["usage_windows"]} == {"five_hour", "seven_day"}


def test_a_flow_emits_provider_usage_after_a_claude_run(client, repo, tmp_path, monkeypatch):
    monkeypatch.setenv("KEEL_FAKE", "0")
    monkeypatch.setenv("KEEL_CLAUDE_BIN", _claude(tmp_path, [json.loads(_lines()[0]), RESULT]))
    tid = start(client, repo, models=CLAUDE)
    wait(client, tid)
    ev = client.bus.of(tid, "provider.usage")[0]          # one per claude run
    assert ev["data"]["provider"] == "claude" and ev["data"]["source"] == "last run"
    assert {w["window"]: w["used_pct"] for w in ev["data"]["windows"]} == {"five_hour": 0.2, "seven_day": 0.05}


# ------------------------------------------------------------------ codex: app-server

def _fake_app_server(tmp_path, answer: str | None) -> str:
    """A stand-in for `codex app-server`: answers initialize, ignores notifications, answers the rate-limit read."""
    reply = (FIX / "codex_rate_limits_read.json").read_text().strip() if answer is None else answer
    body = f"""import json, sys, time
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("id") == 1:
        print(json.dumps({{"id": 1, "result": {{"userAgent": "fake"}}}}), flush=True)
        print(json.dumps({{"method": "account/updated", "params": {{}}}}), flush=True)
    elif msg.get("method") == "account/rateLimits/read":
        if {answer == "hang"!r}:
            time.sleep(30)
        print({reply!r} if {answer != "hang"!r} else "", flush=True)
"""
    f = tmp_path / "app_server.py"
    f.write_text(body)
    return script(tmp_path, "codex-app-server", f'exec "{sys.executable}" {f}\n')


async def test_codex_app_server_client_reads_both_windows(tmp_path):
    res = await usage.codex_rate_limits(binary=_fake_app_server(tmp_path, None))
    assert res["ok"] and res["plan"] == "plus"
    got = {w["window"]: w for w in res["windows"]}
    assert got["five_hour"]["used_pct"] == 0.82 and got["five_hour"]["resets_at"] == 1791218420
    assert got["seven_day"]["used_pct"] == 0.43


async def test_codex_app_server_errors_and_timeouts_are_reported(tmp_path):
    err = await usage.codex_rate_limits(binary=_fake_app_server(tmp_path, json.dumps({"id": 2, "error": {"message": "not logged in"}})))
    assert not err["ok"] and "not logged in" in err["error"]
    t0 = time.monotonic()
    slow = await usage.codex_rate_limits(binary=_fake_app_server(tmp_path, "hang"), timeout=1)
    assert not slow["ok"] and "did not answer" in slow["error"] and time.monotonic() - t0 < 5
    missing = await usage.codex_rate_limits(binary=str(tmp_path / "nope"))
    assert not missing["ok"]


# ------------------------------------------------------------------ copilot: GitHub quota (unofficial)

def test_copilot_parser_on_a_recorded_answer():
    [w] = usage.copilot_windows(json.loads((FIX / "copilot_user.json").read_text()))
    assert (w["window"], w["cap"], w["remaining"], w["used"]) == ("month", 300, 88, 212)
    assert w["used_pct"] == 0.707 and w["resets_at"] == 1793491200          # 2026-11-01T00:00:00Z
    assert usage.copilot_windows({"quota_snapshots": {"premium_interactions": {"unlimited": True}}})[0]["unlimited"]
    assert usage.copilot_windows({"nothing": 1}) == [] and usage.copilot_windows("junk") == []


async def test_copilot_quota_over_http(monkeypatch):
    seen = {}

    def handler(request: httpx.Request):
        seen["auth"] = request.headers.get("authorization")
        if request.headers.get("authorization") == "token bad":
            return httpx.Response(401, json={"message": "Bad credentials"})
        return httpx.Response(200, json=json.loads((FIX / "copilot_user.json").read_text()))
    real = httpx.AsyncClient
    monkeypatch.setattr(usage.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    ok = await usage.copilot_quota("gho_x")
    assert ok["ok"] and ok["windows"][0]["remaining"] == 88 and seen["auth"] == "token gho_x"
    bad = await usage.copilot_quota("bad")
    assert not bad["ok"] and "401" in bad["error"]
    assert not (await usage.copilot_quota(""))["ok"]


def test_providers_usage_endpoint(client, tmp_path, monkeypatch):
    monkeypatch.setenv("KEEL_CODEX_BIN", _fake_app_server(tmp_path, None))     # run as `<bin> app-server`
    r = client.post("/providers/usage", json={"provider": "codex", "key": '{"tokens": {}}'}).json()
    assert r["ok"] and r["source"] == "codex app-server" and len(r["windows"]) == 2
    assert usage.LATEST["codex"]["windows"]
    c = client.post("/providers/usage", json={"provider": "claude"}).json()
    assert not c["ok"] and "next run" in c["error"]


# ------------------------------------------------------------------ the pause rule

def _windows(pct, minutes=41):
    return [{"provider": "claude", "window": "five_hour", "used_pct": pct, "resets_at": int(time.time()) + minutes * 60 + 30,
             "source": "last run", "fetched_at": time.time()}]


def _settings(**kw):
    return {"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause", "simulate_checks": True, **kw}


def test_a_window_at_95_percent_pauses_before_the_next_agent(client, repo, tmp_path, monkeypatch):
    monkeypatch.setenv("KEEL_FAKE", "0")
    monkeypatch.setenv("KEEL_CLAUDE_BIN", _claude(tmp_path, [RESULT]))
    tid = start(client, repo, models=CLAUDE, settings=_settings(provider_windows=_windows(0.96)))
    s = wait(client, tid)
    w = s["waiting"]
    assert w["kind"] == "usage" and w["choices"] == ["continue", "wait", "cheaper", "stop"]
    assert w["title"] == "Claude 5-hour window 96% used, resets in 41 min"
    assert not (tmp_path / "argv.log").exists()          # no agent ran
    s = decide(client, tid, "approve", payload={"choice": "continue"})
    s = wait(client, tid)
    assert s["waiting"]["step"] == "spec_gate"           # the explorer ran, and is not asked again for the same window
    assert (tmp_path / "argv.log").exists()


def test_stop_at_the_usage_pause_ends_the_flow(client, repo, tmp_path, monkeypatch):
    monkeypatch.setenv("KEEL_FAKE", "0")
    monkeypatch.setenv("KEEL_CLAUDE_BIN", _claude(tmp_path, [RESULT]))
    tid = start(client, repo, models=CLAUDE, settings=_settings(provider_windows=_windows(0.99)))
    wait(client, tid)
    decide(client, tid, "reject")
    s = wait(client, tid)
    assert s["status"] == "stopped" and client.bus.of(tid, "budget.stop")


def test_warn_at_80_percent_and_ignore_a_window_that_has_reset(client, repo, tmp_path, monkeypatch):
    monkeypatch.setenv("KEEL_FAKE", "0")
    monkeypatch.setenv("KEEL_CLAUDE_BIN", _claude(tmp_path, [RESULT]))
    tid = start(client, repo, models=CLAUDE, settings=_settings(provider_windows=_windows(0.85)))
    s = wait(client, tid)
    assert s["waiting"]["step"] == "spec_gate"
    [warn] = [e for e in client.bus.of(tid, "budget.warn") if e["data"].get("kind") == "usage"]
    assert warn["data"]["detail"].startswith("Claude 5-hour window 85% used")
    usage.reset()
    usage.absorb(_windows(0.99, minutes=-10))            # reset 10 minutes ago: no longer counts
    assert usage.fullest("claude") is None


def test_api_mode_and_custom_thresholds():
    usage.absorb(_windows(0.7))
    assert usage.fullest("claude")["used_pct"] == 0.7
    assert usage.headline("copilot", {"window": "month", "used_pct": 0.5}) == "Copilot monthly premium requests 50% used"
    assert usage.until(int(time.time()) + 3 * 3600 + 5 * 60 + 30) == "3h 5m"


def test_cheaper_at_the_usage_pause_switches_the_model(client, repo, tmp_path, monkeypatch):
    monkeypatch.setenv("KEEL_FAKE", "0")
    monkeypatch.setenv("KEEL_CLAUDE_BIN", _claude(tmp_path, [RESULT]))
    cheaper = {"provider": "fake", "mode": "api", "model": "fake"}
    tid = start(client, repo, models=CLAUDE, settings=_settings(provider_windows=_windows(0.97), cheaper_model=cheaper))
    wait(client, tid)
    decide(client, tid, "approve", payload={"choice": "cheaper"})
    s = wait(client, tid)
    assert s["status"] == "waiting" and s["waiting"]["kind"] != "usage"
    assert not (tmp_path / "argv.log").exists()          # the claude CLI was not used
