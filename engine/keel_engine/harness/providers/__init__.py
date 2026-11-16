"""The connection a model call goes through: keel's provider names (the person's settings) onto the two API shapes.

    claude    Anthropic Messages API, an Anthropic API key                      AnthropicProvider
    codex     OpenAI chat API, an OpenAI API key                                OpenAICompatProvider
    copilot   GitHub Copilot (the person's plan), GitHub's Copilot SDK, a GitHub sign-in or token  CopilotProvider
    vertex    Google Vertex AI: Claude through AsyncAnthropicVertex, Gemini through its OpenAI-compatible endpoint
    local     a model server on this computer or the network (models/local.py)  OpenAICompatProvider

The provider names stay as keel stored them (claude, codex, copilot) so a saved setting keeps working; the web shows
them as Anthropic, OpenAI and GitHub Copilot (your plan). Anthropic and OpenAI allow a plan's login only in their own
apps, so those rows use API keys. GitHub made the Copilot SDK for apps that work for a person on their Copilot plan,
so the copilot row runs on that (GitHub Models, the old copilot row, was retired by GitHub on 2026-07-30).
"""

from __future__ import annotations

from ...models import catalog
from .base import Provider, ProviderError

LABEL = {"claude": "Anthropic", "codex": "OpenAI", "copilot": "GitHub Copilot", "vertex": "Vertex AI",
         "local": "a local model server"}


def api_model(provider: str, model: str) -> str:
    """The API's model id for keel's short name (opus → claude-opus-5-5); any other id as it is."""
    if provider == "claude":
        return catalog.CLAUDE_API_IDS.get(model, model) or catalog.CLAUDE_API_IDS["sonnet"]
    if provider == "codex":
        return model or "gpt-5.5"
    if provider == "copilot":
        return copilot_model(model)
    return model


def copilot_model(model: str) -> str:
    """A model saved for the retired GitHub Models (openai/gpt-5) or none: Copilot picks one the plan has (auto)."""
    m = (model or "").strip()
    return "auto" if not m or "/" in m else m


def resolve(model: dict, key: str | None) -> Provider:
    """The provider for {provider, model}; `key` is its API key, or (vertex, local) its connection as JSON."""
    from .anthropic import AnthropicProvider, vertex_provider
    from .openai_compat import OpenAICompatProvider

    provider, name = str(model.get("provider") or ""), api_model(str(model.get("provider") or ""), str(model.get("model") or ""))
    if provider == "claude":
        return AnthropicProvider(name, api_key=key)
    if provider == "codex":
        return OpenAICompatProvider(name, api_key=key, who="OpenAI")
    if provider == "copilot":
        from .copilot_sdk import CopilotProvider, sdk

        sdk()                       # a clear error now when the engine has no Copilot SDK
        return CopilotProvider(name, key)
    if provider == "vertex":
        from ...models import vertex

        cfg = vertex.parse(key)
        if not cfg:
            raise ProviderError("Vertex AI is not set up.", hint=vertex.HINT_SETUP)
        name = name or "claude-sonnet-5-5"
        if vertex.is_claude(name):
            return vertex_provider(name, cfg)
        project, region = vertex._need(cfg)
        token = vertex._Token(vertex.credentials(cfg))
        return OpenAICompatProvider(name if "/" in name else f"google/{name}", api_key=token,
                                    base_url=vertex.openai_base(project, region), who="Vertex AI", reasoning=False)
    if provider == "local":
        from ...models import local

        cfg = local.parse(key)
        if not cfg:
            raise ProviderError("No local model server is set up.", hint=local.HINT_SETUP)
        ep, real = local.split(cfg, name)
        return OpenAICompatProvider(real, api_key=ep.get("key") or "local", base_url=local.openai_base(ep),
                                    who=f"the local server {ep.get('name') or ''}".strip(), reasoning=False,
                                    tools=local.tool_support(ep, real))
    raise ProviderError(f"keel has no connection called {provider!r}.",
                        hint="Pick Anthropic, OpenAI, GitHub Copilot, Vertex AI or a local model in Settings › Models & keys.")


__all__ = ["LABEL", "Provider", "ProviderError", "api_model", "copilot_model", "resolve"]
