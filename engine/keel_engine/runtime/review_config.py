"""keel 0.18.65 Settings › Review: the `review:` block of a project's .keel/config.yml.

    review:
      lenses: [correctness, security, performance, consumers-config]   # the ship review's lenses (since before 0.18.65)
      drop_correctness: false        # ship drops its correctness lens after a passed full-diff review (keel v1: on)
      reviewers:                     # the Review page's "Find problems": 1 to 4, lettered A, B, C, D by their place
        - name: correctness and security
          focus: correctness (logic errors, ...) and security (...)
          model: default             # default (the project's) | stronger | {provider, mode, model}
          effort: high               # none: the model's own
          agent: branch-reviewer     # none: keel's review prompt only; else keel's or the project's agent (its role)
          lenses: [data-migration, idempotency-retry, error-handling-retry]
      checker: {model: stronger, effort: null, should_fix: true}       # "is the reviewer right?" on every claim
      fix: {flow: small, model: flow, run_mode: null, one_flow_per_file: false}
      final: {model: stronger, effort: null}                           # the flows' last whole-branch review steps

Nothing written means keel's defaults below: two reviewers (A and B, the focus and lenses they had before 0.18.65), the
checker and the final review on a stronger model than the project's (stronger: Opus at high effort for Claude, high
effort for Codex and Copilot), a confirmed finding fixed by the small flow. The engine owns reading and writing the
file (as runtime/rules_view.py does for the rules); the api gives the project's folder and its models.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

from .. import agent_names, layout
from . import lenses as lenses_mod

CONFIG = ".keel/config.yml"
KEY = "review"
LETTERS = "ABCD"
MAX_REVIEWERS = 4
EFFORTS = ("low", "medium", "high", "xhigh", "max")
FIX_FLOWS = ("quick", "small", "change", "ask")
RUN_MODES = ("manual", "important", "auto")
WORDS = {1: "One", 2: "Two", 3: "Three", 4: "Four"}

FOCUS_A = ("correctness (logic errors, edge cases, null and error handling, concurrency, data loss) and security (auth, "
           "input, data exposure, secrets, injection)")
FOCUS_B = ("tests (would they fail if the code broke? is new behaviour tested?), the ticket or description (does the "
           "change do what it says, anything out of scope?) and design (fits the code around it, simpler way, "
           "performance)")

DEFAULTS: dict[str, Any] = {
    "reviewers": [
        {"name": "correctness and security", "focus": FOCUS_A, "model": "default", "effort": None, "agent": None,
         "lenses": ["data-migration", "idempotency-retry", "error-handling-retry"]},
        {"name": "tests, the description and design", "focus": FOCUS_B, "model": "default", "effort": None, "agent": None,
         "lenses": ["config-secrets", "test-quality"]},
    ],
    "checker": {"model": "stronger", "effort": None, "should_fix": True},
    "fix": {"flow": "small", "model": "flow", "run_mode": None, "one_flow_per_file": False},
    "final": {"model": "stronger", "effort": None},
    "drop_correctness": False,
}
PARTS = ("reviewers", "checker", "fix", "final", "drop_correctness")


class ReviewConfigError(Exception):
    def __init__(self, status: int, error: str):
        super().__init__(error)
        self.status = status
        self.error = error


# ------------------------------------------------------------------ models

def raised(model: dict | None) -> dict:
    """keel's "stronger" for a review: the compiler's next model up (Opus for Claude, high effort for Codex and Copilot),
    and high effort for Claude too. Any other provider keeps its model."""
    from .compiler import stronger      # the compiler imports this module (the final review's model)
    m = dict(model or {})
    if not m:
        return m
    out = stronger(m)
    if out.get("provider") == "claude":
        out["effort"] = "high"
    return out


def resolve(choice: Any, effort: str | None, base: dict | None) -> dict:
    """The model a setting means: default (the [base] the project gives), stronger (raised), or the model it names; the
    effort, when set, wins over the model's."""
    if isinstance(choice, dict) and choice.get("provider") and choice.get("model"):
        m = {k: choice[k] for k in ("provider", "mode", "model", "effort") if choice.get(k)}
    elif choice == "stronger":
        m = raised(base)
    else:
        m = dict(base or {})
    if effort and m:
        m["effort"] = effort
    return m


def final_step(step, agent: str | None) -> bool:
    """A flow's last review: the whole-branch review (branch-reviewer: feature, small, plugin) or a ship lens reviewer."""
    a = agent_names.canonical(agent or "")
    return a == "branch-reviewer" or (a == "lens-reviewer" and (getattr(step, "phase", None) or "") == "ship")


def final_model(root: str | None, model: dict | None) -> dict | None:
    """The model of a final review step that would get [model]: Settings › Review's (keel's default: stronger)."""
    if not model:
        return model
    final = effective(root)["final"]
    return resolve(final.get("model"), final.get("effort"), model)


# ------------------------------------------------------------------ reading

def stored(root: str | None) -> dict:
    """The `review:` block as written ({} when none)."""
    raw = layout.raw_config(root) if root else {}
    block = raw.get(KEY)
    return block if isinstance(block, dict) else {}


def _effort(v: Any) -> str | None:
    s = str(v or "").strip().lower()
    return s if s in EFFORTS else None


def _model(v: Any, fallback: str) -> Any:
    if isinstance(v, dict):
        return {k: str(v[k]) for k in ("provider", "mode", "model", "effort") if v.get(k)} if v.get("provider") and v.get("model") else fallback
    s = str(v or "").strip().lower()
    return s if s in ("default", "stronger") else fallback


def effective(root: str | None) -> dict:
    """keel's defaults with what the project wrote over them (a value keel cannot use falls back to its default)."""
    own = stored(root)
    out = copy.deepcopy(DEFAULTS)
    revs = own.get("reviewers")
    if isinstance(revs, list) and revs:
        clean = []
        for i, r in enumerate(revs[:MAX_REVIEWERS]):
            r = r if isinstance(r, dict) else {}
            d = DEFAULTS["reviewers"][i] if i < len(DEFAULTS["reviewers"]) else {}
            clean.append({
                "name": str(r.get("name") or d.get("name") or f"reviewer {LETTERS[i]}").strip()[:60],
                "focus": str(r.get("focus") or d.get("focus") or "").strip()[:600],
                "model": _model(r.get("model"), "default"),
                "effort": _effort(r.get("effort")),
                "agent": (agent_names.canonical(str(r["agent"]).strip()) or None) if r.get("agent") else None,
                "lenses": [str(x) for x in (r.get("lenses") or []) if isinstance(x, str)],
            })
        out["reviewers"] = clean
    for part in ("checker", "final"):
        p = own.get(part) if isinstance(own.get(part), dict) else {}
        out[part]["model"] = _model(p.get("model"), DEFAULTS[part]["model"])
        out[part]["effort"] = _effort(p.get("effort"))
    ck = own.get("checker") if isinstance(own.get("checker"), dict) else {}
    if isinstance(ck.get("should_fix"), bool):
        out["checker"]["should_fix"] = ck["should_fix"]
    fx = own.get("fix") if isinstance(own.get("fix"), dict) else {}
    if fx.get("flow") in FIX_FLOWS:
        out["fix"]["flow"] = fx["flow"]
    out["fix"]["model"] = fx["model"] if isinstance(fx.get("model"), dict) and fx["model"].get("provider") else "flow"
    out["fix"]["run_mode"] = fx.get("run_mode") if fx.get("run_mode") in RUN_MODES else None
    if isinstance(fx.get("one_flow_per_file"), bool):
        out["fix"]["one_flow_per_file"] = fx["one_flow_per_file"]
    if isinstance(own.get("drop_correctness"), bool):
        out["drop_correctness"] = own["drop_correctness"]
    return out


def find_text(cfg: dict) -> str:
    """What the Review page says above "Find problems": how many reviewers read the change and what each looks at."""
    revs = cfg["reviewers"]
    who = ", ".join(f"{LETTERS[i]}: {r['name']}" for i, r in enumerate(revs))
    n = len(revs)
    verb = "reads" if n == 1 else "read"
    return (f"{WORDS.get(n, str(n))} keel reviewer{'' if n == 1 else 's'} {verb} the change ({who}). Then a fresh run "
            f"checks every serious claim against the code. Only checked findings are shown here; nothing is posted.")


def view(root: str, models: dict | None = None) -> dict:
    """What Settings › Review shows and what the Review page's run needs: the settings, every lens with its reviewer,
    the model each part will use with the project's [models] ({default, reviewer}), the text above Find problems."""
    models = models or {}
    cfg = effective(root)
    raw = layout.raw_config(root)
    owner: dict[str, str] = {}
    for i, r in enumerate(cfg["reviewers"]):
        for n in r["lenses"]:
            owner.setdefault(n, LETTERS[i])
    found = lenses_mod.all_lenses(root)
    lens_rows = [{"name": x["name"], "source": x["source"], "path": x["path"], "title": x["title"],
                  "description": x["description"], "checklist": lenses_mod.flat(x["checklist"])[:2000],
                  "reviewer": owner.get(x["name"])} for x in found]
    known = {x["name"] for x in found}
    default, reviewer = models.get("default"), models.get("reviewer") or models.get("default")
    uses = {
        "reviewers": [resolve(r["model"], r["effort"], default) for r in cfg["reviewers"]],
        "checker": resolve(cfg["checker"]["model"], cfg["checker"]["effort"], default),
        "final": resolve(cfg["final"]["model"], cfg["final"]["effort"], reviewer),
        "fix": cfg["fix"]["model"] if isinstance(cfg["fix"]["model"], dict) else None,
    }
    ship = (raw.get(KEY) or {}).get("lenses") if isinstance(raw.get(KEY), dict) else None
    from .ship import DEFAULT_LENSES
    return {
        "configured": (Path(root) / CONFIG).is_file(),
        "stored": {k: v for k, v in stored(root).items() if k in PARTS},
        "config": cfg,
        "defaults": DEFAULTS,
        "lenses": lens_rows,
        "missing": sorted({n for r in cfg["reviewers"] for n in r["lenses"]} - known),
        "uses": uses,
        "models": {"default": default, "reviewer": reviewer},
        "find_text": find_text(cfg),
        "ship_lenses": [str(x) for x in ship] if isinstance(ship, list) and ship else list(DEFAULT_LENSES),
        "ship_lenses_from": "review.lenses" if isinstance(ship, list) and ship else "keel's default",
        "limits": {"reviewers": MAX_REVIEWERS, "efforts": list(EFFORTS), "fix_flows": list(FIX_FLOWS),
                   "run_modes": list(RUN_MODES)},
    }


# ------------------------------------------------------------------ writing

def _check_model(where: str, v: Any, allowed: tuple[str, ...]) -> None:
    if v is None or (isinstance(v, str) and v in allowed):
        return
    if isinstance(v, dict) and v.get("provider") and v.get("model"):
        return
    raise ReviewConfigError(400, f"{where} is {' or '.join(allowed)}, or a model {{provider, mode, model}}.")


def _check_effort(where: str, v: Any) -> None:
    if v is not None and v not in EFFORTS:
        raise ReviewConfigError(400, f"{where} is one of {', '.join(EFFORTS)}, or none (the model's own).")


def check(root: str, patch: dict) -> dict:
    """The patch, checked: a 400 that says what is wrong, before anything is written."""
    if not isinstance(patch, dict):
        raise ReviewConfigError(400, "The review settings are a map.")
    unknown = set(patch) - set(PARTS) - {"reset"}
    if unknown:
        raise ReviewConfigError(400, f"Unknown review setting: {', '.join(sorted(unknown))}. Settings › Review takes "
                                     f"{', '.join(PARTS)}.")
    revs = patch.get("reviewers")
    if revs is not None:
        if not isinstance(revs, list) or not 1 <= len(revs) <= MAX_REVIEWERS:
            raise ReviewConfigError(400, f"reviewers is a list of 1 to {MAX_REVIEWERS} reviewers.")
        known = set(lenses_mod.names(root))
        seen: dict[str, str] = {}
        for i, r in enumerate(revs):
            who = f"Reviewer {LETTERS[i]}"
            if not isinstance(r, dict):
                raise ReviewConfigError(400, f"{who} is a map {{name, focus, model, effort, agent, lenses}}.")
            if not str(r.get("name") or "").strip():
                raise ReviewConfigError(400, f"{who} needs a name (what it looks at, in a few words).")
            if len(str(r.get("name"))) > 60 or len(str(r.get("focus") or "")) > 600:
                raise ReviewConfigError(400, f"{who}: the name is at most 60 characters, the focus at most 600.")
            if not str(r.get("focus") or "").strip():
                raise ReviewConfigError(400, f"{who} needs a focus: what it reads the change for.")
            _check_model(f"{who}'s model", r.get("model"), ("default", "stronger"))
            _check_effort(f"{who}'s effort", r.get("effort"))
            if r.get("agent") is not None and not isinstance(r.get("agent"), str):
                raise ReviewConfigError(400, f"{who}'s agent is an agent's id, or none.")
            ls = r.get("lenses") or []
            if not isinstance(ls, list) or not all(isinstance(x, str) for x in ls):
                raise ReviewConfigError(400, f"{who}'s lenses is a list of lens names.")
            for n in ls:
                if n not in known:
                    raise ReviewConfigError(400, f'There is no lens "{n}": keel\'s are in content/review-lenses, the '
                                                 f"project's in .keel/lenses.")
                if n in seen:
                    raise ReviewConfigError(400, f'The lens "{n}" is on reviewer {seen[n]} and {LETTERS[i]}: one reviewer each.')
                seen[n] = LETTERS[i]
    for part in ("checker", "final"):
        p = patch.get(part)
        if p is None:
            continue
        if not isinstance(p, dict):
            raise ReviewConfigError(400, f"{part} is a map {{model, effort}}.")
        _check_model(f"{part}.model", p.get("model"), ("default", "stronger"))
        _check_effort(f"{part}.effort", p.get("effort"))
        if "should_fix" in p and not isinstance(p["should_fix"], bool):
            raise ReviewConfigError(400, "checker.should_fix is true or false.")
    fx = patch.get("fix")
    if fx is not None:
        if not isinstance(fx, dict):
            raise ReviewConfigError(400, "fix is a map {flow, model, run_mode, one_flow_per_file}.")
        if fx.get("flow") is not None and fx["flow"] not in FIX_FLOWS:
            raise ReviewConfigError(400, f"fix.flow is one of {', '.join(FIX_FLOWS)}.")
        _check_model("fix.model", fx.get("model"), ("flow",))
        if fx.get("run_mode") is not None and fx["run_mode"] not in RUN_MODES:
            raise ReviewConfigError(400, f"fix.run_mode is one of {', '.join(RUN_MODES)}, or none (the project's).")
        if "one_flow_per_file" in fx and not isinstance(fx["one_flow_per_file"], bool):
            raise ReviewConfigError(400, "fix.one_flow_per_file is true or false.")
    if "drop_correctness" in patch and not isinstance(patch["drop_correctness"], bool):
        raise ReviewConfigError(400, "drop_correctness is true or false.")
    return patch


def _clean_reviewer(r: dict) -> dict:
    out: dict[str, Any] = {"name": str(r["name"]).strip(), "focus": str(r["focus"]).strip()}
    if r.get("model") not in (None, "default"):
        out["model"] = r["model"] if isinstance(r["model"], str) else {k: r["model"][k] for k in ("provider", "mode", "model") if r["model"].get(k)}
    if r.get("effort"):
        out["effort"] = r["effort"]
    if r.get("agent"):
        out["agent"] = agent_names.canonical(str(r["agent"]).strip())
    out["lenses"] = list(r.get("lenses") or [])
    return out


def write(root: str, patch: dict, models: dict | None = None) -> dict:
    """Merge [patch] into the `review:` block of .keel/config.yml and write that block back; every other line stays.
    reset: true takes Settings › Review's parts out (keel's defaults again); the ship's `lenses` stays."""
    check(root, patch)
    f = Path(root) / CONFIG
    if not f.is_file():
        raise ReviewConfigError(409, "This project has no .keel/config.yml yet. Run keel init (Flow page) first: "
                                     "the review settings are kept there.")
    from .rules_view import _blocks
    text = f.read_text()
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ReviewConfigError(409, f".keel/config.yml is not valid YAML ({str(exc).splitlines()[0]}). Fix it by hand first.")
    if not isinstance(raw, dict):
        raise ReviewConfigError(409, ".keel/config.yml is not a YAML map. Fix it by hand first.")
    new = copy.deepcopy(raw.get(KEY)) if isinstance(raw.get(KEY), dict) else {}
    if patch.get("reset"):
        for k in PARTS:
            new.pop(k, None)
    if patch.get("reviewers") is not None:
        new["reviewers"] = [_clean_reviewer(r) for r in patch["reviewers"]]
    for part in ("checker", "final", "fix"):
        if patch.get(part) is not None:
            merged = dict(new.get(part) if isinstance(new.get(part), dict) else {}, **patch[part])
            merged = {k: v for k, v in merged.items() if v is not None and v != DEFAULTS[part].get(k)}
            if merged:
                new[part] = merged
            else:
                new.pop(part, None)
    if "drop_correctness" in patch:
        if patch["drop_correctness"]:
            new["drop_correctness"] = True
        else:
            new.pop("drop_correctness", None)
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    blocks = _blocks(lines)
    dumped = (yaml.safe_dump({KEY: new}, sort_keys=False, default_flow_style=False, allow_unicode=True, width=120)
              .rstrip("\n").split("\n")) if new else []
    if KEY in blocks:
        a, b = blocks[KEY]
        lines = lines[:a] + dumped + lines[b:]
    else:
        lines = lines + dumped
    tmp = f.with_name(f.name + ".keel-tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.replace(tmp, f)
    return view(root, models)


def new_lens(root: str, body: dict, models: dict | None = None) -> dict:
    """Settings › Review's "New lens": .keel/lenses/<slug>.md, and on the reviewer it names (A..D, or none: off)."""
    title = str(body.get("title") or body.get("name") or "").strip()
    name = lenses_mod.slug(body.get("name") or title)
    if not name:
        raise ReviewConfigError(400, "A lens needs a name: a few words, such as Money rounding.")
    who = str(body.get("reviewer") or "").strip().upper()
    cfg = effective(root)
    if who and (who not in LETTERS or LETTERS.index(who) >= len(cfg["reviewers"])):
        raise ReviewConfigError(400, f"There is no reviewer {who}: this project has {len(cfg['reviewers'])}.")
    if who and not (Path(root) / CONFIG).is_file():
        raise ReviewConfigError(409, "This project has no .keel/config.yml yet. Run keel init (Flow page) first.")
    try:
        lens = lenses_mod.write(root, name, title, str(body.get("description") or ""), str(body.get("checklist") or ""))
    except lenses_mod.LensError as e:
        raise ReviewConfigError(e.status, e.error) from e
    if who:
        revs = [dict(r) for r in cfg["reviewers"]]
        i = LETTERS.index(who)
        revs[i]["lenses"] = [*revs[i]["lenses"], lens["name"]]
        write(root, {"reviewers": revs}, models)
    return {"lens": {k: lens[k] for k in ("name", "source", "path", "title", "description")}, "view": view(root, models)}
