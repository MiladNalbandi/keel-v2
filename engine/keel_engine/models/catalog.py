"""Which models each provider offers, and the editable price table the estimator uses.

Prices are USD per million tokens. They are defaults: `$KEEL_DATA/prices.json` overrides any entry,
so a price change is an edit to that file, not a release.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path

from .. import config

log = logging.getLogger(__name__)

MODELS: dict[str, list[dict]] = {
    "fake": [{"id": "fake", "label": "Fake (no network)"}, {"id": "fake-rogue", "label": "Fake that breaks keel rules (tests)"},
             {"id": "fake-lab", "label": "Fake with an ORDER in the spec and an AMEND loop (real checks)"}],
    "claude": [{"id": "opus", "label": "Claude Opus"}, {"id": "sonnet", "label": "Claude Sonnet"}, {"id": "haiku", "label": "Claude Haiku"}],
    "codex": [{"id": "gpt-5.5", "label": "GPT-5.5"}, {"id": "gpt-5.5-mini", "label": "GPT-5.5 mini"}, {"id": "gpt-5-codex", "label": "GPT-5 Codex"}],
    "copilot": [{"id": "gpt-5", "label": "Copilot GPT-5"}, {"id": "claude-sonnet-4.5", "label": "Copilot Claude Sonnet 4.5"},
                {"id": "gpt-4.1", "label": "Copilot GPT-4.1"}],
}

# provider:model -> {in, out, premium}. `premium` = Copilot premium requests per agent call.
DEFAULT_PRICES: dict[str, dict] = {
    "fake:*": {"in": 0.0, "out": 0.0},
    "claude:opus": {"in": 4.0, "out": 20.0},
    "claude:sonnet": {"in": 2.0, "out": 10.0},
    "claude:haiku": {"in": 0.1, "out": 0.5},          # Claude Haiku 5.5 (prompts up to 100K tokens)
    "claude:claude-haiku-4-5": {"in": 1.0, "out": 5.0},
    "claude:*": {"in": 4.0, "out": 20.0},
    "codex:gpt-5.5": {"in": 2.5, "out": 10.0},
    "codex:gpt-5.5-mini": {"in": 0.5, "out": 2.0},
    "codex:*": {"in": 1.25, "out": 10.0},
    # Copilot bills premium requests, not tokens: `premium` is the model's multiplier per request (GitHub's model
    # table; a model "included in the plan" is 0) and `premium_usd` the price of one premium request (GitHub's
    # price for extra requests). Both change at GitHub: override them in prices.json.
    "copilot:gpt-4.1": {"in": 0.0, "out": 0.0, "premium": 0, "premium_usd": 0.04},
    "copilot:gpt-5-mini": {"in": 0.0, "out": 0.0, "premium": 0, "premium_usd": 0.04},
    "copilot:*": {"in": 0.0, "out": 0.0, "premium": 1, "premium_usd": 0.04},
    # a model on the person's own machine costs nothing here; an endpoint can set its own price (local.price)
    "local:*": {"in": 0.0, "out": 0.0},
    # Vertex AI bills the Google Cloud project: Claude about Anthropic's own prices, Gemini Google's (prices.json)
    "vertex:claude-opus-5-5": {"in": 4.0, "out": 20.0},
    "vertex:claude-sonnet-5-5": {"in": 2.0, "out": 10.0},
    "vertex:claude-haiku-5-5": {"in": 0.1, "out": 0.5},
    "vertex:claude-fable-5-1": {"in": 10.0, "out": 50.0},
    "vertex:claude-opus-5": {"in": 5.0, "out": 25.0},
    "vertex:claude-sonnet-5": {"in": 2.0, "out": 10.0},
    "vertex:claude-haiku-4-5@20251001": {"in": 1.0, "out": 5.0},
    "vertex:gemini-3-pro-preview": {"in": 2.0, "out": 12.0},
    "vertex:gemini-3-flash-preview": {"in": 0.5, "out": 3.0},
    "vertex:gemini-2.5-pro": {"in": 1.25, "out": 10.0},
    "vertex:gemini-2.5-flash": {"in": 0.3, "out": 2.5},
    "vertex:gemini-2.5-flash-lite": {"in": 0.1, "out": 0.4},
    "vertex:*": {"in": 2.0, "out": 10.0},
}

# Typical tokens (thousands, in/out) and retry rate per agent, used until there is history.
AGENT_DEFAULTS: dict[str, tuple[float, float, float]] = {
    "explorer": (40, 4, 0.1), "contract-author": (15, 3, 0.1), "test-author": (24, 3, 0.2),
    "implementer": (31, 6, 0.35), "criterion-reviewer": (18, 2, 0.0), "branch-reviewer": (60, 5, 0.1),
    "security-auditor": (45, 4, 0.0), "e2e-author": (35, 6, 0.3), "lens-reviewer": (30, 3, 0.0),
    "librarian": (20, 3, 0.0), "reproducer": (30, 4, 0.3), "investigator": (70, 6, 0.2),
    "web-researcher": (60, 5, 0.1),     # 0.18.20 fetched pages are most of its input
    "pr-reviewer": (35, 3, 0.0),    # review-pr: reads the diff and a few lines around each hunk
    "change-reviewer": (60, 6, 0.0),    # 0.18.68 review-pr standard: the one bug reviewer of the whole change
    "finding-verifier": (30, 3, 0.0),   # review-pr: four claims, the lines each names and the place that decides it
}
FALLBACK_AGENT = (20, 3, 0.1)


# Short names people pick in the UI -> Anthropic API model ids (used by API-key mode).
CLAUDE_API_IDS = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5"}


# provider:model -> context window size, in tokens. Defaults: `provider:*` for a provider's unlisted models.
CONTEXT_WINDOWS: dict[str, int] = {
    "claude:opus": 1_000_000,          # #101 the current Claude models take 1M tokens (Haiku 4.5: 200K)
    "claude:sonnet": 1_000_000,
    "claude:haiku": 1_000_000,
    "claude:claude-haiku-4-5": 200_000,
    "claude:*": 1_000_000,
    "codex:*": 200_000,
    "copilot:*": 200_000,
    "vertex:*": 200_000,
    "local:*": 8_192,      # the server's own window when it says (local.context_for)
}


def context_window(provider: str, model: str) -> int:
    table = CONTEXT_WINDOWS
    return table.get(f"{provider}:{model}") or table.get(f"{provider}:*") or 200_000


def provider_of(model_id: str) -> str | None:
    for provider, items in MODELS.items():
        if any(m["id"] == model_id for m in items):
            return provider
    return None


def prices() -> dict[str, dict]:
    table = dict(DEFAULT_PRICES)
    f = Path(config.data_dir()) / "prices.json"
    if f.is_file():
        try:
            table.update(json.loads(f.read_text()))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("prices.json is not valid JSON, using defaults: %s", exc)
    return table


def price(provider: str, model: str, table: dict | None = None) -> dict:
    table = table or prices()
    return table.get(f"{provider}:{model}") or table.get(f"{provider}:*") or {"in": 0.0, "out": 0.0}


def cost_usd(provider: str, model: str, tokens_in: int, tokens_out: int) -> float:
    p = price(provider, model)
    return round((tokens_in * p.get("in", 0) + tokens_out * p.get("out", 0)) / 1e6, 6)


def premium_cost(provider: str, model: str, requests: int) -> float:
    """0.18.16 what a call's premium requests cost: requests × the model's multiplier × the price of one premium request
    (Copilot). 0 for a provider without premium requests."""
    p = price(provider, model)
    return round(max(0, requests) * float(p.get("premium", 0) or 0) * float(p.get("premium_usd", 0) or 0), 6)


# ------------------------------------------------------------------ catalog (GET /providers/models)
#
# Catalog = {provider: {label, modes: {"api": [{id, label, efforts?}]}, efforts, default: {mode, model, effort?},
#                       source: "builtin" | "server"}}
# #101 the lists are keel's own (every model runs on its API in keel's harness); a local model server's are read from
# it, cached for CACHE_TTL seconds.

CACHE_TTL = 600

CLAUDE_EFFORTS = ["low", "medium", "high", "xhigh", "max"]
CODEX_EFFORTS = ["minimal", "low", "medium", "high"]
# the fake model takes an effort only to record it (tests, trying the pickers without a real model)
FAKE_EFFORTS = ["low", "medium", "high"]

CLAUDE_API = [{"id": "opus", "label": "Claude Opus (claude-opus-5-5)", "efforts": CLAUDE_EFFORTS},
              {"id": "sonnet", "label": "Claude Sonnet (claude-sonnet-5-5)", "efforts": CLAUDE_EFFORTS},
              {"id": "haiku", "label": "Claude Haiku (claude-haiku-5-5)", "efforts": CLAUDE_EFFORTS},
              {"id": "claude-haiku-5-5", "label": "Claude Haiku 5.5", "efforts": CLAUDE_EFFORTS},
              {"id": "claude-fable-5-1", "label": "Claude Fable 5.1", "efforts": CLAUDE_EFFORTS},
              {"id": "claude-opus-5-5", "label": "Claude Opus 5.5", "efforts": CLAUDE_EFFORTS},
              {"id": "claude-opus-5", "label": "Claude Opus 5", "efforts": CLAUDE_EFFORTS},
              {"id": "claude-sonnet-5-5", "label": "Claude Sonnet 5.5", "efforts": CLAUDE_EFFORTS},
              {"id": "claude-sonnet-5", "label": "Claude Sonnet 5", "efforts": CLAUDE_EFFORTS},
              {"id": "claude-haiku-4-5", "label": "Claude Haiku 4.5", "efforts": []}]

CODEX_BUILTIN = [{"id": "gpt-5.5", "label": "GPT-5.5", "efforts": CODEX_EFFORTS},
                 {"id": "gpt-5.5-mini", "label": "GPT-5.5 mini", "efforts": CODEX_EFFORTS},
                 {"id": "gpt-5-codex", "label": "GPT-5 Codex", "efforts": CODEX_EFFORTS}]

GITHUB_MODELS = [{"id": "openai/gpt-5", "label": "OpenAI GPT-5"}, {"id": "openai/gpt-5-mini", "label": "OpenAI GPT-5 mini"},
                 {"id": "openai/gpt-4.1", "label": "OpenAI GPT-4.1"}, {"id": "openai/gpt-4.1-mini", "label": "OpenAI GPT-4.1 mini"},
                 {"id": "deepseek/deepseek-r1", "label": "DeepSeek R1"}, {"id": "meta/llama-4-maverick-17b-128e-instruct-fp8",
                                                                         "label": "Llama 4 Maverick"}]

_cache: dict[tuple, tuple[float, object]] = {}
_lock = threading.Lock()


def clear_cache():
    with _lock:
        _cache.clear()


def _cached(key: tuple, fn):
    """fn() once per CACHE_TTL for this key. A failed probe (None) is cached too, so a missing CLI is not re-run per request."""
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
    try:
        value = fn()
    except Exception as exc:  # a probe must never break the catalog
        log.info("model probe %s failed: %s", key[0], exc)
        value = None
    with _lock:
        _cache[key] = (time.monotonic(), value)
    return value


def efforts_for(provider: str, mode: str, model_id: str) -> list[str] | None:
    """The efforts this provider takes for this model in this mode. [] = it has no effort setting (keel ignores one);
    None = keel cannot tell (Codex: its CLI or API checks the value itself)."""
    m = (model_id or "").lower()
    if provider == "fake":
        return list(FAKE_EFFORTS)
    if provider == "claude":
        # the short name "haiku" is Claude Haiku 5.5 (#101); Haiku 4.5 has no effort
        return [] if re.search(r"haiku-4", m) else list(CLAUDE_EFFORTS)
    if provider == "codex":
        return None
    if provider == "local":
        return []           # a local server has no effort setting keel knows of
    if provider == "vertex":
        from . import vertex

        return vertex.efforts(model_id)
    if provider == "copilot":
        return []           # GitHub Models takes no effort keel knows of
    return []


def effort_for(model: dict | None) -> str | None:
    """The effort a runner passes on: the chosen one when this provider and model take it, else None (the engine's
    own default). Never raises: an effort a model does not have is dropped, not an error."""
    effort = str((model or {}).get("effort") or "").strip().lower()
    if not effort:
        return None
    allowed = efforts_for(str(model.get("provider") or ""), str(model.get("mode") or "api"), str(model.get("model") or ""))
    return effort if allowed is None or effort in allowed else None


def build_catalog(keys: dict | None = None) -> dict:
    """What the person can pick (GET /providers/models). #101 keel's own harness runs every model on its API with an API
    key: one mode, "api", per provider. keys: the providers' connections the api sends (POST /providers/models): `local`
    (the local model servers) and `vertex` (project, region); a provider among them shows only when it is set up."""
    from . import local, vertex

    keys = keys or {}
    try:
        found_local = local.catalog_entry(keys.get("local"))
    except Exception:  # noqa: BLE001 - a server that does not answer leaves its models out, not the catalog
        found_local = None
    out = {
        "claude": {"label": "Anthropic (Claude)", "modes": {"api": CLAUDE_API}, "efforts": CLAUDE_EFFORTS,
                   "default": {"mode": "api", "model": "sonnet", "effort": "high"}, "source": "builtin"},
        "codex": {"label": "OpenAI", "modes": {"api": CODEX_BUILTIN}, "efforts": CODEX_EFFORTS,
                  "default": {"mode": "api", "model": "gpt-5.5", "effort": "medium"}, "source": "builtin"},
        "copilot": {"label": "GitHub Models", "modes": {"api": GITHUB_MODELS}, "efforts": [],
                    "default": {"mode": "api", "model": GITHUB_MODELS[0]["id"]}, "source": "builtin"},
    }
    vx = vertex.catalog_entry(keys.get("vertex"))
    if vx:
        out["vertex"] = vx
    if found_local:
        out["local"] = found_local
    out["fake"] = {"label": "Fake", "modes": {"api": MODELS["fake"]}, "efforts": list(FAKE_EFFORTS),
                   "default": {"mode": "api", "model": "fake"}, "source": "builtin"}
    return out
