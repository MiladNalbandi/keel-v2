"""Model providers: pick the runner for a Model {provider, mode, model, effort}."""

from __future__ import annotations

import os
import tempfile
import time

from .. import config
from ..tools.agent_tools import ToolBox
from .base import AgentRequest, AgentResult, ModelError, Runner

FAKE = {"provider": "fake", "mode": "api", "model": "fake"}
ENV_KEYS = {"claude": "ANTHROPIC_API_KEY", "codex": "OPENAI_API_KEY", "copilot": "GITHUB_TOKEN"}


def effective(model: dict | None) -> dict:
    """KEEL_FAKE=1 forces the fake model everywhere (a fake model stays as chosen, e.g. fake-rogue)."""
    if not model:
        return dict(FAKE)
    if config.fake() and model.get("provider") != "fake":
        return dict(FAKE)
    return dict(model)


def runner_for(model: dict) -> Runner:
    provider, mode = model.get("provider"), model.get("mode", "api")
    if provider == "fake":
        from .fake import FakeRunner
        return FakeRunner()
    if mode == "api":
        from .api_runner import APIRunner
        return APIRunner()
    from . import cli_runners
    if provider == "claude":
        return cli_runners.ClaudeCLIRunner()
    if provider == "codex":
        return cli_runners.CodexCLIRunner()
    if provider == "copilot":
        return cli_runners.OpenCodeRunner() if mode == "opencode" else cli_runners.CopilotCLIRunner()
    raise ModelError(f"Unknown provider '{provider}'.")


def key_for(provider: str, keys: dict | None = None) -> str | None:
    return (keys or {}).get(provider) or os.environ.get(ENV_KEYS.get(provider, "")) or \
        (os.environ.get("GH_TOKEN") if provider == "copilot" else None)


def login_keys(provider: str, mode: str, key: str | None) -> dict:
    """POST /providers/test: a key given for a subscription CLI is its login, under the same names StartThread.keys uses."""
    if not key or mode == "api":
        return {}
    if provider == "claude":
        return {"claude_oauth": key}
    if provider == "copilot":
        return {"copilot": key}
    return {}


async def test_provider(provider: str, mode: str, model: str, key: str | None = None) -> dict:
    """Send "Reply with exactly: OK" and report how it went (POST /providers/test)."""
    m = effective({"provider": provider, "mode": mode, "model": model})
    t0 = time.monotonic()
    ms = lambda: int((time.monotonic() - t0) * 1000)  # noqa: E731
    try:
        with tempfile.TemporaryDirectory(prefix="keel-test-") as tmp:
            req = AgentRequest(agent="test", system="", prompt="Reply with exactly: OK", root=tmp, phase="none", model=m,
                               toolbox=ToolBox(tmp, "none"), key=key or key_for(m["provider"]), workdir=tmp, timeout=120,
                               keys=login_keys(m["provider"], m.get("mode", "api"), key))
            if m["provider"] != "fake" and m.get("mode") == "api":
                from .api_runner import _text, chat_model
                reply = await chat_model(m["provider"], m.get("model", ""), req.key).ainvoke(req.prompt)
                text = _text(reply.content)
            else:
                res: AgentResult = await runner_for(m).run(req, lambda *a, **k: None)
                text = res.text
        return {"ok": "OK" in (text or ""), "text": (text or "")[:200], "ms": ms()}
    except ModelError as exc:
        return {"ok": False, "ms": ms(), "error": f"{exc}{(' ' + exc.hint) if exc.hint else ''}"}
    except Exception as exc:
        return {"ok": False, "ms": ms(), "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
