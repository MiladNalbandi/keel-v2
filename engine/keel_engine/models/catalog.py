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
    "claude:haiku": {"in": 1.0, "out": 5.0},
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
CLAUDE_API_IDS = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-4-5"}


# provider:model -> context window size, in tokens. Defaults: `provider:*` for a provider's unlisted models.
CONTEXT_WINDOWS: dict[str, int] = {
    "claude:opus": 200_000,
    "claude:sonnet": 200_000,
    "claude:haiku": 200_000,
    "claude:*": 200_000,
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
# opencode run --variant: github-copilot's Claude and GPT models take these, newer GPT-5 ones also xhigh, Gemini none
OPENCODE_EFFORTS = ["low", "medium", "high"]
# the fake model takes an effort only to record it (tests, trying the pickers without a real model)
FAKE_EFFORTS = ["low", "medium", "high"]

CLAUDE_SUBSCRIPTION = [{"id": "opus", "label": "Claude Opus", "efforts": CLAUDE_EFFORTS},
                       {"id": "sonnet", "label": "Claude Sonnet", "efforts": CLAUDE_EFFORTS},
                       {"id": "haiku", "label": "Claude Haiku", "efforts": []}]
CLAUDE_API = [{"id": "opus", "label": "Claude Opus (claude-opus-5-5)", "efforts": CLAUDE_EFFORTS},
              {"id": "sonnet", "label": "Claude Sonnet (claude-sonnet-5-5)", "efforts": CLAUDE_EFFORTS},
              {"id": "haiku", "label": "Claude Haiku (claude-haiku-4-5)", "efforts": []},
              {"id": "claude-fable-5-1", "label": "Claude Fable 5.1", "efforts": CLAUDE_EFFORTS},
              {"id": "claude-opus-5-5", "label": "Claude Opus 5.5", "efforts": CLAUDE_EFFORTS},
              {"id": "claude-opus-5", "label": "Claude Opus 5", "efforts": CLAUDE_EFFORTS},
              {"id": "claude-sonnet-5-5", "label": "Claude Sonnet 5.5", "efforts": CLAUDE_EFFORTS},
              {"id": "claude-sonnet-5", "label": "Claude Sonnet 5", "efforts": CLAUDE_EFFORTS},
              {"id": "claude-haiku-4-5", "label": "Claude Haiku 4.5", "efforts": []}]

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


def copilot_efforts_from_help(text: str) -> list[str]:
    """`copilot --help`'s `--reasoning-effort <level>` (newer Copilot CLIs): its "[possible values: ...]" in order, else []."""
    at = (text or "").find("--reasoning-effort")
    if at < 0:
        return []
    block = re.split(r"\n\s*--?[a-z]", (text or "")[at + 2:], maxsplit=1)[0]   # up to the next option
    m = re.search(r"\[possible values:\s*([^\]]+)\]", block)
    if not m:
        return []
    return [v.strip() for v in m.group(1).split(",") if re.fullmatch(r"[a-z]+", v.strip())]


def copilot_cli_efforts() -> list[str]:
    """The efforts the Copilot CLI here takes (its --help, asked once per CACHE_TTL); [] when it has no such option."""
    exe = _bin("copilot")
    if not exe:
        return []
    return _cached(("copilot-cli-efforts", exe), lambda: copilot_efforts_from_help(_run([exe, "--help"]) or "")) or []


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
    return [{k: v for k, v in m.items() if k in ("id", "label", "efforts", "badge", "tools", "context")} for m in models]


def _efforts_union(models: list[dict]) -> list[str]:
    out: list[str] = []
    for m in models:
        for e in m.get("efforts") or []:
            if e not in out:
                out.append(e)
    return out


def opencode_efforts(model_id: str) -> list[str]:
    """`opencode run --variant` for a github-copilot model (opencode's own table): Gemini none, Claude low..high,
    GPT low..high and xhigh from GPT-5.1 on."""
    m = (model_id or "").lower()
    if "gemini" in m or not m:
        return []
    if "claude" in m:
        return list(OPENCODE_EFFORTS)
    return OPENCODE_EFFORTS + (["xhigh"] if re.search(r"gpt-(5\.[1-9]|[6-9])", m) else [])


def efforts_for(provider: str, mode: str, model_id: str) -> list[str] | None:
    """The efforts this provider takes for this model in this mode. [] = it has no effort setting (keel ignores one);
    None = keel cannot tell (Codex: its CLI or API checks the value itself)."""
    m = (model_id or "").lower()
    if provider == "fake":
        return list(FAKE_EFFORTS)
    if provider == "claude":
        return [] if "haiku" in m and not re.search(r"haiku-[5-9]", m) else list(CLAUDE_EFFORTS)
    if provider == "codex":
        return None
    if provider == "local":
        return []           # a local server has no effort setting keel knows of
    if provider == "vertex":
        from . import vertex

        return vertex.efforts(model_id)
    if provider == "copilot":
        if mode == "opencode":
            return opencode_efforts(model_id)
        # the Copilot CLI took no effort until --reasoning-effort came (1.0.x): whatever its --help lists
        return copilot_cli_efforts() if mode == "subscription" else []
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
    """keys: the providers' connections the api sends (POST /providers/models): `local` (the local model servers) and
    `vertex` (project, region). A provider among them shows only when it is set up."""
    from . import local, vertex

    keys = keys or {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {"codex": pool.submit(probe_codex), "copilot": pool.submit(probe_copilot), "opencode": pool.submit(probe_opencode),
                   "local": pool.submit(local.catalog_entry, keys.get("local"))}
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
    cli_efforts = copilot_cli_efforts()
    sub = [{**m, "efforts": cli_efforts} for m in (sub or COPILOT_BUILTIN)] if cli_efforts else sub
    opencode = [{**m, "efforts": opencode_efforts(m["id"])} if opencode_efforts(m["id"]) else dict(m) for m in (oc or COPILOT_BUILTIN)]
    copilot = {"label": "GitHub Copilot",
               "modes": {"subscription": sub or COPILOT_BUILTIN, "opencode": opencode, "api": GITHUB_MODELS},
               "efforts": [], "default": {"mode": "subscription", "model": (sub or COPILOT_BUILTIN)[0]["id"]},
               "source": "cli" if (sub or oc) else "builtin"}

    fake = {"label": "Fake", "modes": {"api": MODELS["fake"]}, "efforts": list(FAKE_EFFORTS), "default": {"mode": "api", "model": "fake"},
            "source": "builtin"}
    out = {"claude": claude, "codex": codex, "copilot": copilot}
    vx = vertex.catalog_entry(keys.get("vertex"))
    if vx:
        out["vertex"] = vx
    if found["local"]:
        out["local"] = found["local"]
    out["fake"] = fake
    return out
