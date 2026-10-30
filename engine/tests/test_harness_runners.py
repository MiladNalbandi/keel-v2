"""keel 0.19.0 harness mode: Copilot CLI and OpenCode continue their own session (as claude --resume and codex exec
resume do), so a harness flow's plan and build run in one session on every harness."""

import dataclasses
import json

from keel_engine.models.cli_runners import CopilotCLIRunner, OpenCodeRunner

from test_effort import req
from test_v03 import script

COPILOT = {"provider": "copilot", "mode": "subscription", "model": "claude-sonnet-4.5"}
OPENCODE = {"provider": "copilot", "mode": "opencode", "model": "claude-sonnet-4.5"}


async def argv_for(tmp_path, monkeypatch, tool, runner, r, body="") -> list[str]:
    """Run a runner against a stand-in CLI that writes down its arguments (and prints `body`)."""
    monkeypatch.setenv(f"KEEL_{tool.upper()}_BIN", script(
        tmp_path, tool, f'cat >/dev/null\nfor a in "$@"; do echo "$a"; done > {tmp_path}/argv\n{body}'))
    try:
        await runner.run(r, lambda *a, **k: None)
    except Exception:  # noqa: BLE001 - the stand-in answers little; its arguments matter here
        pass
    return (tmp_path / "argv").read_text().splitlines()


async def test_copilot_names_a_new_session_and_continues_it(tmp_path, monkeypatch):
    first = dataclasses.replace(req(tmp_path, COPILOT), system="You plan.", session="s-1")
    argv = await argv_for(tmp_path, monkeypatch, "copilot", CopilotCLIRunner(), first)
    assert argv[argv.index("--session-id") + 1] == "s-1"
    assert argv[argv.index("-p") + 1].startswith("You plan.")              # a new session gets the role
    again = dataclasses.replace(first, resume=True)
    argv = await argv_for(tmp_path, monkeypatch, "copilot", CopilotCLIRunner(), again)
    assert argv[argv.index("--session-id") + 1] == "s-1"
    assert argv[argv.index("-p") + 1] == "go"                              # the session has it already


async def test_copilot_without_a_session_runs_as_before(tmp_path, monkeypatch):
    argv = await argv_for(tmp_path, monkeypatch, "copilot", CopilotCLIRunner(), req(tmp_path, COPILOT))
    assert "--session-id" not in argv


async def test_opencode_continues_its_session_and_reports_a_new_ones_id(tmp_path, monkeypatch):
    got = []
    r = dataclasses.replace(req(tmp_path, OPENCODE), on_session=got.append)
    line = json.dumps({"type": "text", "sessionID": "ses_9", "part": {"type": "text", "text": "planned"}})
    argv = await argv_for(tmp_path, monkeypatch, "opencode", OpenCodeRunner(), r, f"echo '{line}'\n")
    assert "-s" not in argv and got == ["ses_9"]
    again = dataclasses.replace(r, session="ses_9", resume=True)
    argv = await argv_for(tmp_path, monkeypatch, "opencode", OpenCodeRunner(), again, f"echo '{line}'\n")
    assert argv[argv.index("-s") + 1] == "ses_9" and argv[-1] == "go"
    assert got == ["ses_9"]                                                # the same session: not reported again
