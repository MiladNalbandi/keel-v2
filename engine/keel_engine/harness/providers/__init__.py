"""The connection a model call goes through: keel's provider names (the person's settings) onto the two API shapes.

    claude    Anthropic Messages API, an Anthropic API key                      AnthropicProvider
    codex     OpenAI chat API, an OpenAI API key                                OpenAICompatProvider
    copilot   GitHub Models (models.github.ai), a GitHub token with models:read OpenAICompatProvider
    vertex    Google Vertex AI: Claude through AsyncAnthropicVertex, Gemini through its OpenAI-compatible endpoint
    local     a model server on this computer or the network (models/local.py)  OpenAICompatProvider

The provider names stay as keel stored them (claude, codex, copilot) so a saved setting keeps working; the web shows
them as Anthropic, OpenAI and GitHub Models. No subscription login reaches a model from here: Anthropic, OpenAI and
GitHub allow a plan's login only in their own apps and approved partners, so keel's harness uses API keys.
"""

from __future__ import annotations

import re

from ...models import catalog
from .base import Provider, ProviderError

LABEL = {"claude": "Anthropic", "codex": "OpenAI", "copilot": "GitHub Models", "vertex": "Vertex AI", "local": "a local model server"}


def api_model(provider: str, model: str) -> str:
    """The API's model id for keel's short name (opus → claude-opus-5-5); any other id as it is."""
    if provider == "claude":
        return catalog.CLAUDE_API_IDS.get(model, model) or catalog.CLAUDE_API_IDS["sonnet"]
    if provider == "codex":
        return model or "gpt-5.5"
    if provider == "copilot":
        # GitHub Models names a model with its maker (openai/gpt-5); a model saved for the Copilot CLI said gpt-5
        if model and "/" not in model and re.match(r"(gpt-|o\d)", model):
            return f"openai/{model}"
        return model or "openai/gpt-5"
    return model


def resolve(model: dict, key: str | None) -> Provider:
    """The provider for {provider, model}; `key` is its API key, or (vertex, local) its connection as JSON."""
    from .anthropic import AnthropicProvider, vertex_provider
    from .openai_compat import GITHUB_MODELS_URL, OpenAICompatProvider

    provider, name = str(model.get("provider") or ""), api_model(str(model.get("provider") or ""), str(model.get("model") or ""))
    if provider == "claude":
        return AnthropicProvider(name, api_key=key)
    if provider == "codex":
        return OpenAICompatProvider(name, api_key=key, who="OpenAI")
    if provider == "copilot":
        return OpenAICompatProvider(name, api_key=key, base_url=GITHUB_MODELS_URL, who="GitHub Models")
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
                        hint="Pick Anthropic, OpenAI, GitHub Models, Vertex AI or a local model in Settings › Models & keys.")


__all__ = ["LABEL", "Provider", "ProviderError", "api_model", "resolve"]
