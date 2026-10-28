"""keel 0.18.9: the thinking effort an agent runs with. Each runner passes it the way its engine takes it (Claude Code
--effort, Codex -c model_reasoning_effort, OpenCode --variant, the Anthropic API's output_config.effort, OpenAI's
reasoning_effort); a provider or model without an effort setting gets none; the fake model records it; every agent
call says which effort it used (agent.started)."""

from __future__ import annotations

import pytest

from keel_engine.models import api_runner, catalog
from keel_engine.models.base import AgentRequest
from keel_engine.models.cli_runners import ClaudeCLIRunner, CodexCLIRunner, CopilotCLIRunner, OpenCodeRunner
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


def test_effort_for_keeps_only_what_the_model_takes(monkeypatch):
    monkeypatch.setenv("KEEL_COPILOT_BIN", "/nonexistent/copilot")   # a Copilot CLI on this machine must not count
    eff = catalog.effort_for
    assert eff({"provider": "claude", "mode": "subscription", "model": "sonnet", "effort": "xhigh"}) == "xhigh"
    assert eff({"provider": "claude", "mode": "api", "model": "claude-opus-5-5", "effort": "MAX"}) == "max"
    assert eff({"provider": "claude", "mode": "subscription", "model": "haiku", "effort": "high"}) is None
    assert eff({"provider": "claude", "mode": "subscription", "model": "sonnet", "effort": "ultra"}) is None
    assert eff({"provider": "claude", "mode": "subscription", "model": "sonnet"}) is None
    assert eff({"provider": "codex", "mode": "subscription", "model": "gpt-6-sol", "effort": "ultra"}) == "ultra"
    assert eff({"provider": "copilot", "mode": "subscription", "model": "gpt-5", "effort": "high"}) is None
    assert eff({"provider": "copilot", "mode": "api", "model": "openai/gpt-5", "effort": "high"}) is None
    assert eff({"provider": "copilot", "mode": "opencode", "model": "claude-sonnet-4.5", "effort": "high"}) == "high"
    assert eff({"provider": "copilot", "mode": "opencode", "model": "gemini-2.5-pro", "effort": "high"}) is None
    assert eff({"provider": "copilot", "mode": "opencode", "model": "gpt-5", "effort": "xhigh"}) is None
    assert eff({"provider": "copilot", "mode": "opencode", "model": "gpt-5.2", "effort": "xhigh"}) == "xhigh"
    assert eff({"provider": "fake", "mode": "api", "model": "fake", "effort": "low"}) == "low"
    assert eff(None) is None


def test_the_catalog_says_which_models_take_an_effort(monkeypatch):
    monkeypatch.setenv("KEEL_COPILOT_BIN", "/nonexistent/copilot")   # a Copilot CLI on this machine must not count
    cat = catalog.build_catalog()
    claude = {m["id"]: m.get("efforts") for m in cat["claude"]["modes"]["subscription"]}
    assert claude["sonnet"] == ["low", "medium", "high", "xhigh", "max"] and claude["haiku"] == []
    assert cat["fake"]["efforts"] == ["low", "medium", "high"]
    for m in cat["copilot"]["modes"]["opencode"]:
        assert m.get("efforts", []) == catalog.opencode_efforts(m["id"])
    assert all("efforts" not in m for m in cat["copilot"]["modes"]["subscription"]) and cat["copilot"]["efforts"] == []


async def test_claude_code_gets_the_effort_flag(tmp_path, monkeypatch):
    argv = await argv_of(tmp_path, monkeypatch, "claude", ClaudeCLIRunner(),
                         {"provider": "claude", "mode": "subscription", "model": "opus", "effort": "max"})
    assert argv[argv.index("--effort") + 1] == "max" and argv[argv.index("--model") + 1] == "opus"


async def test_claude_code_without_an_effort_or_on_haiku_gets_no_flag(tmp_path, monkeypatch):
    for model in ({"provider": "claude", "mode": "subscription", "model": "sonnet"},
                  {"provider": "claude", "mode": "subscription", "model": "haiku", "effort": "high"}):
        assert "--effort" not in await argv_of(tmp_path, monkeypatch, "claude", ClaudeCLIRunner(), model)


async def test_codex_drops_nothing_it_cannot_check(tmp_path, monkeypatch):
    argv = await argv_of(tmp_path, monkeypatch, "codex", CodexCLIRunner(),
                         {"provider": "codex", "mode": "subscription", "model": "gpt-6-sol", "effort": "xhigh"})
    assert argv[argv.index("model_reasoning_effort=xhigh") - 1] == "-c"
    plain = await argv_of(tmp_path, monkeypatch, "codex", CodexCLIRunner(), {"provider": "codex", "mode": "subscription", "model": "gpt-5.5"})
    assert not any(a.startswith("model_reasoning_effort") for a in plain)


async def test_opencode_gets_the_variant_before_the_prompt(tmp_path, monkeypatch):
    argv = await argv_of(tmp_path, monkeypatch, "opencode", OpenCodeRunner(),
                         {"provider": "copilot", "mode": "opencode", "model": "claude-sonnet-4.5", "effort": "high"})
    assert argv[argv.index("--variant") + 1] == "high" and argv[-1] == "go"
    gemini = await argv_of(tmp_path, monkeypatch, "opencode", OpenCodeRunner(),
                           {"provider": "copilot", "mode": "opencode", "model": "gemini-2.5-pro", "effort": "high"})
    assert "--variant" not in gemini


def test_the_api_runners_pass_the_effort_as_their_api_takes_it():
    claude = api_runner.chat_model("claude", "opus", "sk-test", "xhigh")
    assert claude.output_config == {"effort": "xhigh"} and claude.model == "claude-opus-5-5"
    assert not api_runner.chat_model("claude", "opus", "sk-test", None).output_config
    codex = api_runner.chat_model("codex", "gpt-5.5", "sk-test", "high")
    assert codex.reasoning_effort == "high"
    assert api_runner.chat_model("codex", "gpt-5.5", "sk-test").reasoning_effort is None


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


def test_the_copilot_clis_help_says_which_efforts_it_takes():
    assert catalog.copilot_efforts_from_help(COPILOT_HELP) == ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
    one_line = "  --reasoning-effort <level>  Set the reasoning effort level [possible values: low, medium, high]\n"
    assert catalog.copilot_efforts_from_help(one_line) == ["low", "medium", "high"]
    assert catalog.copilot_efforts_from_help("  --model <model>  Set the AI model [possible values: a, b]\n") == []
    assert catalog.copilot_efforts_from_help("") == []
    no_values = "  --reasoning-effort <level>  Set the effort\n  --context <tier>  [possible values: default, long_context]\n"
    assert catalog.copilot_efforts_from_help(no_values) == []


def test_a_copilot_cli_with_reasoning_effort_offers_it_and_one_without_does_not(tmp_path, monkeypatch):
    copilot_cli(tmp_path, monkeypatch, COPILOT_HELP)
    model = {"provider": "copilot", "mode": "subscription", "model": "claude-sonnet-4.5", "effort": "high"}
    assert catalog.effort_for(model) == "high"
    assert catalog.effort_for({**model, "effort": "ultra"}) is None
    assert catalog.effort_for({**model, "mode": "api", "model": "openai/gpt-5"}) is None
    subscription = catalog.build_catalog()["copilot"]["modes"]["subscription"]
    assert subscription and all(m["efforts"] == ["none", "minimal", "low", "medium", "high", "xhigh", "max"] for m in subscription)
    old = tmp_path / "old"
    old.mkdir()
    copilot_cli(old, monkeypatch, "Usage: copilot [options]\n  --model <model>  Set the AI model to use\n")
    assert catalog.effort_for(model) is None


async def test_copilot_cli_gets_the_reasoning_effort_flag(tmp_path, monkeypatch, repo):
    copilot_cli(tmp_path, monkeypatch, COPILOT_HELP)
    model = {"provider": "copilot", "mode": "subscription", "model": "claude-sonnet-4.5", "effort": "medium"}
    try:
        await CopilotCLIRunner().run(req(tmp_path, model), lambda *a, **k: None)
    except Exception:  # noqa: BLE001 - the stand-in answers nothing; only its arguments matter here
        pass
    argv = (tmp_path / "argv").read_text().splitlines()
    assert argv[argv.index("--reasoning-effort") + 1] == "medium" and argv[argv.index("--model") + 1] == "claude-sonnet-4.5"
    try:
        await CopilotCLIRunner().run(req(tmp_path, {**model, "effort": None}), lambda *a, **k: None)
    except Exception:  # noqa: BLE001
        pass
    assert "--reasoning-effort" not in (tmp_path / "argv").read_text().splitlines()


async def test_copilot_in_a_read_only_run_is_told_it_has_no_shell_and_which_tools_read_instead(tmp_path, monkeypatch, repo):
    # keel 0.18.63 KeelBot's Ask with Copilot: every shell command is refused (no guard hook), and the model spent its
    # turns on `cat` and `git diff` that could never run. Now the prompt says so up front and names keel's git tools.
    copilot_cli(tmp_path, monkeypatch, COPILOT_HELP)
    model = {"provider": "copilot", "mode": "subscription", "model": "claude-sonnet-4.5"}
    ro = AgentRequest(agent="helper", system="", prompt="why does sonar fail?", root=str(tmp_path), phase=None, model=model,
                      toolbox=ToolBox(str(tmp_path), "none", readonly=True), workdir=str(tmp_path))
    try:
        await CopilotCLIRunner().run(ro, lambda *a, **k: None)
    except Exception:  # noqa: BLE001 - the stand-in answers nothing; only its arguments matter here
        pass
    argv = (tmp_path / "argv").read_text()
    assert "--deny-tool\nshell" in argv and "--deny-tool\nwrite" in argv
    assert "Shell commands are not available" in argv and "git_diff" in argv
    try:
        await CopilotCLIRunner().run(req(tmp_path, model), lambda *a, **k: None)
    except Exception:  # noqa: BLE001
        pass
    assert "Shell commands are not available" not in (tmp_path / "argv").read_text()
