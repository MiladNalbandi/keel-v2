"""Which models each provider offers, and the editable price table the estimator uses.

Prices are USD per million tokens. They are defaults: `$KEEL_DATA/prices.json` overrides any entry,
so a price change is an edit to that file, not a release.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .. import config

log = logging.getLogger(__name__)

MODELS: dict[str, list[dict]] = {
    "fake": [{"id": "fake", "label": "Fake (no network)"}, {"id": "fake-rogue", "label": "Fake that breaks keel rules (tests)"}],
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
    "claude:haiku": {"in": 1.0, "out": 5.0},
    "claude:*": {"in": 4.0, "out": 20.0},
    "codex:gpt-5.5": {"in": 2.5, "out": 10.0},
    "codex:gpt-5.5-mini": {"in": 0.5, "out": 2.0},
    "codex:*": {"in": 1.25, "out": 10.0},
    "copilot:*": {"in": 0.0, "out": 0.0, "premium": 1},
}

# Typical tokens (thousands, in/out) and retry rate per agent, used until there is history.
AGENT_DEFAULTS: dict[str, tuple[float, float, float]] = {
    "explorer": (40, 4, 0.1), "contract-author": (15, 3, 0.1), "test-author": (24, 3, 0.2),
    "implementer": (31, 6, 0.35), "ac-reviewer": (18, 2, 0.0), "code-reviewer": (60, 5, 0.1),
    "security-auditor": (45, 4, 0.0), "e2e-author": (35, 6, 0.3), "reviewer": (30, 3, 0.0),
    "librarian": (20, 3, 0.0), "reproducer": (30, 4, 0.3), "investigator": (70, 6, 0.2),
}
FALLBACK_AGENT = (20, 3, 0.1)


# Short names people pick in the UI -> Anthropic API model ids (used by API-key mode).
CLAUDE_API_IDS = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-4-5"}


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
