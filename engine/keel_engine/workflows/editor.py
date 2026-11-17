"""The workflow editor (Build › Workflows): each error at its line, the file as keel writes it, and the starter.

The same jobs rules/packs.py does for rule packs: error_lines maps validate()'s and load_yaml()'s messages to the YAML
line of the step (and the key) they name; normalize writes the documented key order with a 2-space indent; starter is
the small, valid workflow a new one begins with.
"""

from __future__ import annotations

import json
import re

import yaml

from ..rules.packs import _Dumper, _tidy
from .model import Step, slug

# the documented order: a workflow's head (the api's WorkflowDoc order), then a step's (id, kind, name, phase, then
# what the kind needs in the Step model's order, instructions last; content/workflows/fix.yaml says the same)
HEAD_ORDER = ("id", "name", "source", "version", "about", "group", "start", "est_tokens", "mcp", "needs_plugins", "based_on", "keel_rules",
              "own_branch", "budget", "steps")
STEP_ORDER = ("id", "kind", "name", "phase",
              *[f.alias or k for k, f in Step.model_fields.items() if k not in ("id", "kind", "name", "phase", "instructions")],
              "instructions")
# words in a message that name a step key
_ALIAS = {"lane": "lanes", "choice": "choices", "item": "per_item"}
_KEYS = sorted({*STEP_ORDER, *_ALIAS}, key=len, reverse=True)
_STEP = re.compile(r"^(?:Step|Gate) '([^']+)'(?: \([^)]*\))?:?\s*(.*)$", re.S)
_DUP = re.compile(r"^Step id '([^']+)' is used more than once")
_LOC = re.compile(r"^([A-Za-z_]+(?:\.[A-Za-z0-9_]+)*): ")


# ---------------------------------------------------------------- errors at their line

def _get(node, key):
    """The (key node, value node) of `key` in a mapping node, or None."""
    if isinstance(node, yaml.MappingNode):
        for k, v in node.value:
            if getattr(k, "value", None) == key or (key == "no" and getattr(k, "value", None) in ("no", "No", "NO")):
                return k, v
    return None


def _steps(root) -> list:
    hit = _get(root, "steps")
    return hit[1].value if hit and isinstance(hit[1], yaml.SequenceNode) else []


def _step_id(node) -> str | None:
    hit = _get(node, "id")
    return str(hit[1].value) if hit and isinstance(hit[1], yaml.ScalarNode) else None


def _line(node) -> int:
    return node.start_mark.line + 1


def _key_line(step, rest: str) -> int:
    """The line of the first key the message names that this step has; else the step's own line."""
    found = []
    for word in _KEYS:
        m = re.search(rf"(?<![\w.]){re.escape(word)}(?![\w])", rest)
        key = _ALIAS.get(word, word)
        if m and _get(step, key):
            found.append((m.start(), key))
    if found:
        return _line(_get(step, min(found)[1])[0])
    return _line(step)


def _path_line(root, parts: list[str]) -> int | None:
    """The line of `steps.2.agent` (pydantic's 0-based loc): the deepest node of the path that exists."""
    node, line = root, None
    for p in parts:
        if isinstance(node, yaml.MappingNode):
            hit = _get(node, p)
            if not hit:
                break
            line, node = _line(hit[0]), hit[1]
        elif isinstance(node, yaml.SequenceNode) and p.isdigit() and int(p) < len(node.value):
            node = node.value[int(p)]
            line = _line(node)
        else:
            break
    return line


def _one(root, steps: list, by_id: dict, e: str) -> list[dict]:
    m = _DUP.match(e)
    if m:
        seen = [s for s in steps if _step_id(s) == m.group(1)]
        return [{"line": _line(seen[-1] if seen else root), "message": e}]
    m = _STEP.match(e)
    if m:
        step = by_id.get(m.group(1))
        if step is not None:
            return [{"line": _key_line(step, m.group(2)), "message": e}]
        hit = _get(root, "steps")          # a locked step that is gone: where the steps are
        return [{"line": _line(hit[0]) if hit else 1, "message": e}]
    if e.startswith("The 'for each AC' steps"):
        flags = [bool((_get(s, "per_ac") or (None, None))[1] is not None and str(_get(s, "per_ac")[1].value).lower() == "true")
                 for s in steps]
        first = flags.index(True) if True in flags else 0
        gap = next((n for n in range(first + 1, len(flags)) if flags[n] and not flags[n - 1]), first)
        return [{"line": _line(steps[gap]) if steps else 1, "message": e}]
    if e.startswith("The workflow has no steps"):
        hit = _get(root, "steps")
        return [{"line": _line(hit[0]) if hit else 1, "message": e}]
    # pydantic's "steps.0.kind: ...; steps.1.name: ..." (one message from load_yaml), or "needs_plugins: ..."
    parts = [p for p in e.split("; ")] if all(_LOC.match(p) for p in e.split("; ")) else [e]
    out = []
    for p in parts:
        lm = _LOC.match(p)
        line = _path_line(root, lm.group(1).split(".")) if lm else None
        out.append({"line": line or 1, "message": p})
    return out


def error_lines(text: str, errors: list[str]) -> list[dict]:
    """[{line, message}] for the errors validate_yaml gave: the step (and the key) each names, else the first line. One
    message from load_yaml that lists several fields ("steps.0.kind: ...; steps.2.name: ...") gives one line each."""
    if not errors:
        return []
    try:
        root = yaml.compose(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
        short = " ".join(str(x) for x in (getattr(exc, "context", None), getattr(exc, "problem", None)) if x)
        return [{"line": (mark.line + 1) if mark else 1,
                 "message": f"The YAML does not parse: {short}." if short and e.startswith("The YAML does not parse") else e}
                for e in errors]
    if root is None:
        return [{"line": 1, "message": e} for e in errors]
    steps = _steps(root)
    by_id: dict = {}
    for s in steps:
        sid = _step_id(s)
        if sid is not None and sid not in by_id:
            by_id[sid] = s
    out: list[dict] = []
    for e in errors:
        out += _one(root, steps, by_id, e)
    return out


# ---------------------------------------------------------------- the file as keel writes it

_QUOTED = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"\\]|\\.)*\"")


def _head_and_body(text: str) -> tuple[list[str], list[str]]:
    lines = str(text).split("\n")
    head: list[str] = []
    n = 0
    while n < len(lines) and (lines[n].startswith("#") or (not lines[n].strip() and head)):
        head.append(lines[n])
        n += 1
    while head and not head[-1].strip():
        head.pop()
    return head, lines[n:]


def _has_comment(lines: list[str]) -> bool:
    """A comment after the head: `# …` on its own line or after a value (quoted text does not count). A `#` in a block
    of text counts too, which only means keel leaves the file as it is."""
    return any(re.search(r"(^|\s)#", _QUOTED.sub("", ln)) for ln in lines)


def _str(dumper, data: str):
    if "\n" in data.rstrip("\n"):
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    if len(data) > 80:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=">")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


class _WfDumper(_Dumper):
    pass


_WfDumper.add_representer(str, _str)


def _ordered(d: dict, order: tuple) -> dict:
    return {**{k: d[k] for k in order if k in d}, **{k: v for k, v in d.items() if k not in order}}


def _dump(value) -> str:
    return yaml.dump(_tidy(value), Dumper=_WfDumper, sort_keys=False, indent=2, width=120, allow_unicode=True,
                     default_flow_style=False)


def normalize(text: str) -> str:
    """The workflow as keel writes it on save: its leading comment lines kept, the head's keys and each step's keys in
    the documented order, 2-space indent, a blank line between steps, short lists on one line. Keys it does not know
    stay after the known ones. A file with comments below its head is left as it is (keel keeps every comment), and
    so is one that does not parse."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return text
    if not isinstance(data, dict):
        return text
    head, body = _head_and_body(text)
    if _has_comment(body):
        return text if text.endswith("\n") else text + "\n"
    steps = data.get("steps")
    if steps is not None and not isinstance(steps, list):
        return text
    top = _ordered({k: v for k, v in data.items() if k != "steps"}, HEAD_ORDER)
    out = ("\n".join(head) + "\n\n" if head else "") + (_dump(top) if top else "")
    if steps is not None:
        items = []
        for st in steps:
            if isinstance(st, dict):
                st = _ordered({("no" if k is False else k): v for k, v in st.items()}, STEP_ORDER)
            items.append("\n".join("  " + ln if ln else ln for ln in _dump([st]).rstrip("\n").split("\n")))
        out += "\nsteps:\n\n" + "\n\n".join(items) + "\n" if items else "\nsteps: []\n"
    return out


# ---------------------------------------------------------------- the starter

STARTER = """\
# {title}: a small workflow to start from. Change it, then Save.
# Steps run from the top down. An agent step asks an agent (it costs tokens), a code step runs plain code (no tokens),
# and a gate waits for a person. per_ac: true runs a step once for each acceptance criterion: red, then green.
# With keel rules on, the locked steps (lock: true) stay. The fields of a workflow: docs/CONTRACT.md (Workflow keys).

id: {id}
name: {name}
version: 1
keel_rules: {keel_rules}

steps:

  - id: plan
    kind: agent
    name: plan
    phase: spec
    agent: explorer
    model: default
    instructions: >-
      Read the request and the code it touches. Write a short plan and the acceptance criteria, one per line.

  - id: plan_ok
    kind: gate
    name: plan approval
    phase: spec
    back: plan
    lock: true

  - id: red
    kind: agent
    name: red
    phase: red
    agent: test-author
    model: default
    per_ac: true

  - id: verify_red
    kind: code
    name: verify red + commit
    phase: red
    action: verify_red+commit
    per_ac: true
    lock: true

  - id: green
    kind: agent
    name: green
    phase: green
    agent: implementer
    model: default
    per_ac: true

  - id: verify_green
    kind: code
    name: verify green + commit
    phase: green
    action: verify_green+commit
    per_ac: true
    lock: true

  - id: done
    kind: gate
    name: final review
    phase: final-review
    lock: true
"""


def _scalar(text: str) -> str:
    """`text` as a YAML value: plain when it reads back the same, else double-quoted."""
    try:
        if text and yaml.safe_load(f"k: {text}") == {"k": text} and "#" not in text:
            return text
    except yaml.YAMLError:
        pass
    return json.dumps(text, ensure_ascii=False)


def starter(name: str = "My workflow", wid: str | None = None, keel_rules: bool = True) -> str:
    """The new workflow's YAML: valid as it is, in keel's style (the help names each field)."""
    name = (name or "").strip() or "My workflow"
    return STARTER.format(title=name, name=_scalar(name), id=_scalar(slug(wid or name)), keel_rules="true" if keel_rules else "false")
