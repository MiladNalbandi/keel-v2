"""keel 0.18.9: the thinking effort an agent runs with. Each runner passes it the way its engine takes it (Claude Code
--effort, Codex -c model_reasoning_effort, OpenCode --variant, the Anthropic API's output_config.effort, OpenAI's
reasoning_effort); a provider or model without an effort setting gets none; the fake model records it; every agent
call says which effort it used (agent.started)."""

from __future__ import annotations

import pytest

from keel_engine.models import catalog
from keel_engine.models.base import AgentRequest
from keel_engine.models.fake import FakeRunner
from keel_engine.tools.agent_tools import ToolBox

from test_v03 import collect, script


def req(tmp_path, model: dict) -> AgentRequest:
    return AgentRequest(agent="implementer", system="", prompt="go", root=str(tmp_path), phase="green", model=model,
                        toolbox=ToolBox(str(tmp_path), "green"), workdir=str(tmp_path))


async def argv_of(tmp_path, monkeypatch, tool: str, runner, model: dict) -> list[str]:
    """Run a runner against a stand-in CLI that only writes down its arguments."""
    monkeypatch.setenv(f"KEEL_{tool.upper()}_BIN", script(tmp_path, tool, f'cat >/dev/null\nfor a in "$@"; do echo "$a"; done > {tmp_path}/argv\n'))
    try:
        await runner.run(req(tmp_path, model), lambda *a, **k: None)
    except Exception:  # noqa: BLE001 - the stand-in answers nothing; only its arguments matter here
        pass
    return (tmp_path / "argv").read_text().splitlines()


def test_effort_for_keeps_only_what_the_model_takes():
    eff = catalog.effort_for
    assert eff({"provider": "claude", "mode": "api", "model": "sonnet", "effort": "xhigh"}) == "xhigh"
    assert eff({"provider": "claude", "mode": "api", "model": "claude-opus-5-5", "effort": "MAX"}) == "max"
    assert eff({"provider": "claude", "mode": "api", "model": "haiku", "effort": "high"}) == "high"      # Claude Haiku 5.5
    assert eff({"provider": "claude", "mode": "api", "model": "claude-haiku-4-5", "effort": "high"}) is None
    assert eff({"provider": "claude", "mode": "subscription", "model": "sonnet", "effort": "ultra"}) is None
    assert eff({"provider": "claude", "mode": "api", "model": "sonnet"}) is None
    assert eff({"provider": "codex", "mode": "api", "model": "gpt-6-sol", "effort": "ultra"}) == "ultra"
    # #101 GitHub Models takes none, also for a model once saved for the Copilot CLI or OpenCode
    assert eff({"provider": "copilot", "mode": "api", "model": "openai/gpt-5", "effort": "high"}) is None
    assert eff({"provider": "copilot", "mode": "opencode", "model": "claude-sonnet-4.5", "effort": "high"}) is None
    assert eff({"provider": "fake", "mode": "api", "model": "fake", "effort": "low"}) == "low"
    assert eff(None) is None


def test_the_catalog_says_which_models_take_an_effort():
    cat = catalog.build_catalog()
    claude = {m["id"]: m.get("efforts") for m in cat["claude"]["modes"]["api"]}
    assert claude["sonnet"] == ["low", "medium", "high", "xhigh", "max"] == claude["haiku"]
    assert claude["claude-haiku-4-5"] == []
    assert cat["fake"]["efforts"] == ["low", "medium", "high"]
    assert set(cat["copilot"]["modes"]) == {"api"} and cat["copilot"]["efforts"] == []


async def test_the_fake_model_records_the_effort(tmp_path):
    steps, emit = collect()
    await FakeRunner().run(req(tmp_path, {"provider": "fake", "mode": "api", "model": "fake", "effort": "high"}), emit)
    assert {"kind": "thinking", "text": "Thinking effort: high."} in steps
    steps, emit = collect()
    await FakeRunner().run(req(tmp_path, {"provider": "fake", "mode": "api", "model": "fake"}), emit)
    assert not any("Thinking effort" in s["text"] for s in steps)


@pytest.mark.parametrize("effort, said", [("medium", "medium"), (None, None)])
def test_each_agent_call_says_its_effort(client, repo, effort, said):
    from conftest import start, wait

    model = {"provider": "fake", "mode": "api", "model": "fake", **({"effort": effort} if effort else {})}
    tid = start(client, repo, models={"default": model})
    wait(client, tid)
    started = [e["data"] for e in client.bus.of(tid, "agent.started")]
    assert started and all(d["effort"] == said for d in started)


COPILOT_HELP = """Usage: copilot [options]
      --model <model>
          Set the AI model to use
      --reasoning-effort <level>
          Set the reasoning effort level [possible values: none, minimal, low, medium, high, xhigh, max]
      --context <tier>
          Set the context window tier (overrides persisted setting) [possible values: default, long_context]
      --auto-tier <preference>
"""  # Copilot CLI 1.0.101's layout: each option's text on the next line, the next option's values close by


def copilot_cli(tmp_path, monkeypatch, help_text: str) -> None:
    """A stand-in Copilot CLI: its --help prints help_text, a run writes down its arguments."""
    (tmp_path / "help.txt").write_text(help_text)
    monkeypatch.setenv("KEEL_COPILOT_BIN", script(tmp_path, "copilot",
        f'if [ "$1" = "--help" ]; then cat {tmp_path}/help.txt; exit 0; fi\n'
        f'cat >/dev/null\nfor a in "$@"; do echo "$a"; done > {tmp_path}/argv\n'))

