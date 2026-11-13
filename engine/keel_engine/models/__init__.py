"""Model providers: pick the runner for a Model {provider, mode, model, effort}.

#101 every real model runs in keel's own harness (keel_engine/harness), on its provider's API with an API key. The
coding-agent apps (Claude Code, Codex, Copilot CLI, OpenCode) no longer run: a model saved with a subscription or
OpenCode mode runs on its provider's API key like any other (`mode` is read as "api").
"""

from __future__ import annotations

import os
import time

from .. import config
from .base import AgentRequest, AgentResult, ModelError, Runner
from .catalog import effort_for as catalog_effort

FAKE = {"provider": "fake", "mode": "api", "model": "fake"}
ENV_KEYS = {"claude": "ANTHROPIC_API_KEY", "codex": "OPENAI_API_KEY", "copilot": "GITHUB_TOKEN"}


def effective(model: dict | None) -> dict:
    """KEEL_FAKE=1 forces the fake model everywhere (a fake model stays as chosen, e.g. fake-rogue). Every other model
    runs on its API (#101): its mode is "api" whatever was saved."""
    if not model:
        return dict(FAKE)
    if config.fake() and model.get("provider") != "fake":
        return dict(FAKE)
    return {**model, "mode": "api"}


def runner_for(model: dict) -> Runner:
    if model.get("provider") == "fake":
        from .fake import FakeRunner
        return FakeRunner()
    from ..harness import HarnessRunner

    return HarnessRunner()


def key_for(provider: str, keys: dict | None = None) -> str | None:
    return (keys or {}).get(provider) or os.environ.get(ENV_KEYS.get(provider, "")) or \
        (os.environ.get("GH_TOKEN") if provider == "copilot" else None)


async def _one_answer(m: dict, system: str, prompt: str, key: str | None) -> tuple[str, int, int]:
    """One question to the model through the harness's provider, no tools: (text, tokens in, tokens out)."""
    import asyncio

    from ..harness import providers
    from ..harness.providers.base import ProviderError, Request

    try:
        provider = await asyncio.to_thread(providers.resolve, m, key)
        if m["provider"] == "local":     # the review check's and the Doctor's prompt, packed into the window
            system, prompt = await _fit_local(key, m.get("model", ""), system, prompt)
        turn = await provider.turn(Request(system=system, messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
                                           tools=[], max_tokens=16_000, effort=catalog_effort(m)), lambda *a, **k: None)
    except ProviderError as exc:
        raise ModelError(str(exc), exc.hint) from exc
    return turn.text(), turn.usage.total_in, turn.usage.output


async def ask(model: dict, system: str, prompt: str, keys: dict | None = None, timeout: int = 300) -> dict:
    """One question to a model, no tools (POST /agents/ask; used by the api's Doctor).

    keys follow StartThread.keys: {provider: api key}. The fake model has nothing to say here: it answers with fake=True
    and the caller uses its own rules.
    """
    import asyncio

    m = effective(model)
    t0 = time.monotonic()
    ms = lambda: int((time.monotonic() - t0) * 1000)  # noqa: E731
    if m["provider"] == "fake":
        return {"ok": True, "fake": True, "text": "", "tokens_in": 0, "tokens_out": 0, "ms": ms()}
    try:
        text, tin, tout = await asyncio.wait_for(_one_answer(m, system, prompt, key_for(m["provider"], keys)), timeout)
        return {"ok": True, "fake": False, "text": text, "tokens_in": tin, "tokens_out": tout, "ms": ms()}
    except ModelError as exc:
        return {"ok": False, "fake": False, "text": "", "ms": ms(), "error": f"{exc}{(' ' + exc.hint) if exc.hint else ''}"}
    except Exception as exc:
        return {"ok": False, "fake": False, "text": "", "ms": ms(), "error": f"{type(exc).__name__}: {str(exc)[:300]}"}


async def _fit_local(key: str | None, model: str, system: str, prompt: str) -> tuple[str, str]:
    import asyncio

    from . import local

    ep, name = local.split(local.parse(key), model)
    window = await asyncio.to_thread(local.context_for, ep, name)
    system, _h, prompt, _t = local.fit(system, [], prompt, window)
    return system, prompt


async def test_provider(provider: str, mode: str, model: str, key: str | None = None, effort: str | None = None) -> dict:
    """Send "Reply with exactly: OK" and report how it went (POST /providers/test). `mode` is kept for old callers."""
    import asyncio

    m = effective({"provider": provider, "mode": "api", "model": model, **({"effort": effort} if effort else {})})
    t0 = time.monotonic()
    ms = lambda: int((time.monotonic() - t0) * 1000)  # noqa: E731
    try:
        if m["provider"] == "fake":
            res: AgentResult = await runner_for(m).run(_fake_request(m), lambda *a, **k: None)
            text = res.text
        else:
            text, _i, _o = await asyncio.wait_for(_one_answer(m, "", "Reply with exactly: OK", key or key_for(m["provider"])), 120)
        return {"ok": "OK" in (text or ""), "text": (text or "")[:200], "ms": ms()}
    except ModelError as exc:
        return {"ok": False, "ms": ms(), "error": f"{exc}{(' ' + exc.hint) if exc.hint else ''}"}
    except Exception as exc:
        return {"ok": False, "ms": ms(), "error": f"{type(exc).__name__}: {str(exc)[:300]}"}


def _fake_request(m: dict) -> AgentRequest:
    import tempfile

    from ..tools.agent_tools import ToolBox

    tmp = tempfile.mkdtemp(prefix="keel-test-")
    return AgentRequest(agent="test", system="", prompt="Reply with exactly: OK", root=tmp, phase="none", model=m,
                        toolbox=ToolBox(tmp, "none"), workdir=tmp, timeout=120)
