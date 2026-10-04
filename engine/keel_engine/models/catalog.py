"""Which models each provider offers, and the editable price table the estimator uses.

Prices are USD per million tokens. They are defaults: `$KEEL_DATA/prices.json` overrides any entry,
so a price change is an edit to that file, not a release.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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


# ------------------------------------------------------------------ catalog (GET /providers/models)
#
# Catalog = {provider: {label, modes: {mode: [{id, label, efforts?}]}, efforts, default: {mode, model, effort?},
#                       source: "cli" | "cache" | "builtin"}}
# Lists come from the installed CLIs where possible (`codex debug models` or codex's models_cache.json,
# `copilot --help`, `opencode models github-copilot`), else from the built-in lists below. CLI answers are
# cached for CACHE_TTL seconds and no probe may take longer than PROBE_TIMEOUT.

CACHE_TTL = 600
PROBE_TIMEOUT = 4.0
DEADLINE = 5.0

CLAUDE_EFFORTS = ["low", "medium", "high", "xhigh", "max"]
CODEX_EFFORTS = ["minimal", "low", "medium", "high"]

CLAUDE_SUBSCRIPTION = [{"id": "opus", "label": "Claude Opus", "efforts": CLAUDE_EFFORTS},
                       {"id": "sonnet", "label": "Claude Sonnet", "efforts": CLAUDE_EFFORTS},
                       {"id": "haiku", "label": "Claude Haiku"}]
CLAUDE_API = [{"id": "opus", "label": "Claude Opus (claude-opus-5-5)", "efforts": CLAUDE_EFFORTS},
              {"id": "sonnet", "label": "Claude Sonnet (claude-sonnet-5-5)", "efforts": CLAUDE_EFFORTS},
              {"id": "haiku", "label": "Claude Haiku (claude-haiku-4-5)"},
              {"id": "claude-fable-5-1", "label": "Claude Fable 5.1", "efforts": CLAUDE_EFFORTS},
              {"id": "claude-opus-5-5", "label": "Claude Opus 5.5", "efforts": CLAUDE_EFFORTS},
              {"id": "claude-opus-5", "label": "Claude Opus 5", "efforts": CLAUDE_EFFORTS},
              {"id": "claude-sonnet-5-5", "label": "Claude Sonnet 5.5", "efforts": CLAUDE_EFFORTS},
              {"id": "claude-sonnet-5", "label": "Claude Sonnet 5", "efforts": CLAUDE_EFFORTS},
              {"id": "claude-haiku-4-5", "label": "Claude Haiku 4.5"}]

CODEX_BUILTIN = [{"id": "gpt-5.5", "label": "GPT-5.5", "efforts": CODEX_EFFORTS},
                 {"id": "gpt-5.5-mini", "label": "GPT-5.5 mini", "efforts": CODEX_EFFORTS},
                 {"id": "gpt-5-codex", "label": "GPT-5 Codex", "efforts": CODEX_EFFORTS}]

COPILOT_BUILTIN = [{"id": "claude-sonnet-4.5", "label": "Claude Sonnet 4.5"}, {"id": "gpt-5", "label": "GPT-5"},
                   {"id": "gpt-5-mini", "label": "GPT-5 mini"}, {"id": "claude-haiku-4.5", "label": "Claude Haiku 4.5"},
                   {"id": "gpt-4.1", "label": "GPT-4.1"}]
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


def _bin(tool: str) -> str | None:
    import shutil

    return os.environ.get(f"KEEL_{tool.upper()}_BIN") or shutil.which(tool)


def _run(argv: list[str], env: dict | None = None) -> str | None:
    from .cli import safe_env

    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=PROBE_TIMEOUT, stdin=subprocess.DEVNULL,
                           env=env or safe_env())
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None


def _label(slug: str) -> str:
    return slug.replace("-", " ").replace("gpt", "GPT").strip()


def codex_models_from(data) -> list[dict] | None:
    """Codex's model list ({"models": [{slug, display_name, visibility, supported_reasoning_levels, priority}]})."""
    items = data.get("models") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return None
    out = []
    for m in sorted((m for m in items if isinstance(m, dict)), key=lambda m: m.get("priority", 999)):
        slug = m.get("slug") or m.get("id")
        if not slug or m.get("visibility", "list") != "list":
            continue
        item = {"id": slug, "label": m.get("display_name") or _label(slug)}
        efforts = [r.get("effort") for r in m.get("supported_reasoning_levels") or [] if isinstance(r, dict) and r.get("effort")]
        if efforts:
            item["efforts"] = efforts
        if m.get("default_reasoning_level"):
            item["default_effort"] = m["default_reasoning_level"]
        if m.get("supported_in_api") is False:
            item["api"] = False
        out.append(item)
    return out or None


def _codex_home_env() -> dict:
    from .cli import safe_env

    env = safe_env()
    if not env.get("CODEX_HOME"):
        private = config.data_dir() / "codex-home"
        if (private / "auth.json").is_file():
            env["CODEX_HOME"] = str(private)
    return env


def codex_cache_files() -> list[Path]:
    files = []
    if os.environ.get("CODEX_HOME"):
        files.append(Path(os.environ["CODEX_HOME"]) / "models_cache.json")
    files.append(config.data_dir() / "codex-home" / "models_cache.json")
    files.append(Path.home() / ".codex" / "models_cache.json")
    return files


def probe_codex() -> tuple[list[dict], str] | None:
    exe = _bin("codex")
    if exe:
        out = _cached(("codex-cli", exe), lambda: _run([exe, "debug", "models"], _codex_home_env()))
        if out:
            try:
                models = codex_models_from(json.loads(out))
            except json.JSONDecodeError:
                models = None
            if models:
                return models, "cli"
    for f in codex_cache_files():
        if f.is_file():
            try:
                models = codex_models_from(json.loads(f.read_text()))
            except (OSError, json.JSONDecodeError):
                models = None
            if models:
                return models, "cache"
    return None


QUOTED = re.compile(r'"([A-Za-z0-9][\w.\-:/]*)"')


def copilot_models_from_help(text: str) -> list[str]:
    """Model ids from `copilot --help` (`--model <model>  ... (choices: "a", "b")`) or `copilot help config`
    (a `model` entry followed by a list of quoted ids)."""
    out: list[str] = []

    def add(chunk: str):
        for q in QUOTED.findall(chunk):
            if q not in out:
                out.append(q)

    for m in re.finditer(r"--model\b", text):
        chunk = text[m.end():]
        nxt = re.search(r"\n\s*-{1,2}[A-Za-z]", chunk)
        chunk = chunk[:nxt.start()] if nxt else chunk[:2000]
        c = re.search(r"choices:(.*)", chunk, re.S)
        if c:
            add(c.group(1))
    if not out:
        m = re.search(r"^\s*`?model`?\s*:.*?(?:\n\s*\n|\Z)", text, re.M | re.S)
        if m:
            add(m.group(0))
    return out


def probe_copilot() -> list[dict] | None:
    exe = _bin("copilot")
    if not exe:
        return None

    def ask():
        for argv in ([exe, "--help"], [exe, "help", "config"]):
            ids = copilot_models_from_help(_run(argv) or "")
            if ids:
                return ids
        return None

    ids = _cached(("copilot-cli", exe), ask)
    return [{"id": i, "label": _label(i)} for i in ids] if ids else None


def opencode_models_from(text: str) -> list[str]:
    ids = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("github-copilot/"):
            ids.append(line.split("/", 1)[1])
    return ids


def probe_opencode() -> list[dict] | None:
    exe = _bin("opencode")
    if not exe:
        return None
    ids = _cached(("opencode-cli", exe), lambda: opencode_models_from(_run([exe, "models", "github-copilot"]) or "") or None)
    return [{"id": i, "label": _label(i)} for i in ids] if ids else None


def _public(models: list[dict]) -> list[dict]:
    return [{k: v for k, v in m.items() if k in ("id", "label", "efforts")} for m in models]


def _efforts_union(models: list[dict]) -> list[str]:
    out: list[str] = []
    for m in models:
        for e in m.get("efforts") or []:
            if e not in out:
                out.append(e)
    return out


def build_catalog() -> dict:
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {"codex": pool.submit(probe_codex), "copilot": pool.submit(probe_copilot), "opencode": pool.submit(probe_opencode)}
        deadline = time.monotonic() + DEADLINE
        found = {}
        for name, fut in futures.items():
            try:
                found[name] = fut.result(timeout=max(deadline - time.monotonic(), 0.01))
            except Exception:
                found[name] = None

    claude = {"label": "Claude", "modes": {"subscription": CLAUDE_SUBSCRIPTION, "api": CLAUDE_API}, "efforts": CLAUDE_EFFORTS,
              "default": {"mode": "subscription", "model": "sonnet", "effort": "high"}, "source": "builtin"}

    if found["codex"]:
        models, source = found["codex"]
        api = [m for m in models if m.get("api") is not False] or CODEX_BUILTIN
        first = models[0]
        codex_default = {"mode": "subscription", "model": first["id"]}
        if first.get("default_effort"):
            codex_default["effort"] = first["default_effort"]
        codex = {"label": "GPT / Codex", "modes": {"subscription": _public(models), "api": _public(api)},
                 "efforts": _efforts_union(models) or CODEX_EFFORTS, "default": codex_default, "source": source}
    else:
        codex = {"label": "GPT / Codex", "modes": {"subscription": CODEX_BUILTIN, "api": CODEX_BUILTIN}, "efforts": CODEX_EFFORTS,
                 "default": {"mode": "subscription", "model": "gpt-5.5", "effort": "medium"}, "source": "builtin"}

    sub, oc = found["copilot"], found["opencode"]
    copilot = {"label": "GitHub Copilot",
               "modes": {"subscription": sub or COPILOT_BUILTIN, "opencode": oc or COPILOT_BUILTIN, "api": GITHUB_MODELS},
               "efforts": [], "default": {"mode": "subscription", "model": (sub or COPILOT_BUILTIN)[0]["id"]},
               "source": "cli" if (sub or oc) else "builtin"}

    fake = {"label": "Fake", "modes": {"api": MODELS["fake"]}, "efforts": [], "default": {"mode": "api", "model": "fake"},
            "source": "builtin"}
    return {"claude": claude, "codex": codex, "copilot": copilot, "fake": fake}
