"""Where to look: keel looks up the names in a question in the code graph itself (no model, no tool call) and gives the
agent a short list of line ranges to read first.

Agents that explored the graph with its MCP tools spent more tokens, not fewer (tools/codegraph.py): every tool answer
stays in the conversation and every turn sends the conversation again. Here the lookup costs nothing: keel reads the
index it already has in memory (runtime/codegraph_view.py) and the agent starts with the places, so it can skip the
search turns and read ranges instead of whole files.

    terms(text)          the words of a question that can name code: identifiers, file names, plain words (>= 4)
    where_to_look(...)   the prompt block, or "" when the project has no index or nothing matches

A symbol scores by how its name meets the terms (exact beats a prefix beats a part), what kind it is (a class or a
function before a field), and whether the person pointed at it (@mention, the open file); tests are left out unless the
question is about tests or the person points at one. At most LIMIT symbols, at most PER_FILE from one file, each with up to USERS places that use it.
"""

from __future__ import annotations

import re
from collections import defaultdict

from . import codegraph_view

LIMIT = 12
PER_FILE = 4
USERS = 3
CALLS = 3
GENERIC = 6          # a name more symbols have than this ("create") says little by itself
SPECIFIC = 12        # a word in at most this many class names (or 5% of them in a big project) tells them apart
SPECIFIC_SHARE = 0.05
MAX_CHARS = 2600
SKIP_KINDS = {"import", "namespace", "enum_member", "parameter", "file"}
KIND_BONUS = {"class": 3, "interface": 3, "enum": 2, "function": 3, "method": 3, "route": 4, "component": 3,
              "struct": 2, "trait": 2, "type_alias": 1, "constant": 1, "module": 1, "property": 0, "field": 0}
STOP = {"this", "that", "with", "what", "where", "when", "which", "does", "from", "into", "have", "there", "their",
        "they", "them", "then", "than", "should", "would", "could", "about", "after", "before", "also", "just", "only",
        "make", "each", "every", "show", "tell", "file", "files", "code", "line", "lines", "find", "need", "want",
        "please", "explain", "change", "add", "test", "tests", "work", "works", "used", "using", "data", "value",
        "values", "return", "returns", "string", "number", "list", "true", "false", "null", "none", "error"}
# words that never join into a name ("the key" is not TheKey)
JOIN_STOP = STOP | {"the", "and", "for", "are", "was", "has", "its", "our", "you", "can", "not", "but", "how", "why",
                    "who", "all", "any", "one"}
MEMBER_KINDS = {"method", "field", "property", "variable", "constructor"}
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:[./][A-Za-z_][A-Za-z0-9_]*)*")
_CODE_LIKE = re.compile(r"[a-z][A-Z]|_|\.|/|^[A-Z][a-z]+[A-Z]")


def terms(text: str) -> list[tuple[str, bool]]:
    """[(term, code_like)] in order, lower-cased; code-like = an identifier or a path, not a plain word. A plain plural
    also gives its singular ("sprites": sprite), and two plain words next to each other give their join ("API key":
    apikey), so the words people use meet the names in the code (both only match a name exactly)."""
    seen: dict[str, bool] = {}
    ticked = {t.lower() for t in re.findall(r"`([^`\s]{2,80})`", text or "")}
    plain: list[str] = []
    for raw in _IDENT.findall(text or ""):
        for part in {raw, raw.split("/")[-1], raw.split(".")[0]} if ("/" in raw or "." in raw) else {raw}:
            low = part.lower()
            code = bool(_CODE_LIKE.search(part)) or low in ticked
            if not code and part == raw:
                plain.append(low)
            if len(low) < 3 or (not code and (len(low) < 4 or low in STOP)):
                continue
            seen[low] = seen.get(low, False) or code
            if not code and len(low) >= 5 and low.endswith("s") and not low.endswith("ss"):
                seen.setdefault(low[:-1], False)
    for a, b in zip(plain, plain[1:]):
        # a join is specific enough to match the start of a name too ("api key": ApiKeyAuthenticationFilter)
        if len(a) >= 3 and len(b) >= 3 and len(a + b) >= 6 and a not in JOIN_STOP and b not in JOIN_STOP:
            seen.setdefault(a + b, True)
    return list(seen.items())


def _test_path(path: str) -> bool:
    p = path.lower()
    return bool(re.search(r"(^|/)(tests?|spec|__tests__)/|(_test|\.test|\.spec|test_)[^/]*$|tests?\.[a-z]+$", p))


def where_to_look(pid: str, text: str, *, mentions: list[dict] | None = None, open_file: str | None = None,
                  selection: dict | None = None) -> str:
    """The "Where to look" block for this question, or "" (no index, nothing matched)."""
    try:
        g, _ = codegraph_view.load(pid)
    except codegraph_view.NoGraph:
        return ""
    except Exception:  # a broken index never stops an answer
        return ""
    words = terms(" ".join([text or ""] + [str(m.get("value") or "") for m in mentions or []]))
    if not words and not open_file and not mentions:
        return ""
    about_tests = bool(re.search(r"\btests?\b|\bspec\b", text or "", re.I))
    by_name: dict[str, list[str]] = defaultdict(list)
    for nid, n in g.nodes.items():
        if n["kind"] not in SKIP_KINDS and n.get("name"):
            by_name[n["name"].lower()].append(nid)
    unit_names = [g.nodes[u]["name"].lower() for u in set(g.unit.values()) if g.nodes[u].get("name")]
    low_text = (text or "").lower()
    # the classes the question names as code ("WaveAuthoringController"; a join like "API key" does not count)
    named_units = {g.unit.get(nid, nid) for t, code in words if code and t in low_text for nid in by_name.get(t, ())
                   if about_tests or not _test_path(g.nodes[nid]["file"] or "")}
    near = set(named_units)
    for (a, b) in g.unit_links:
        if a in named_units:
            near.add(b)
        if b in named_units:
            near.add(a)
    most = max(SPECIFIC, int(len(unit_names) * SPECIFIC_SHARE))
    specific = [t for t, _ in words if len(t) >= 4 and sum(t in n for n in unit_names) <= most]

    def fits(nid: str) -> bool:
        """The symbol's class (or, for a top-level function, its file) fits the question: near a class the question
        names, else its name holds a specific word of the question (SpriteController.upload for "sprites ... upload")."""
        u = g.unit.get(nid, nid)
        if named_units:
            return u in near
        owner = g.nodes[u]["name"] if u != nid else (g.nodes[nid]["file"] or "").rsplit("/", 1)[-1].split(".")[0]
        return any(t in owner.lower() for t in specific)

    score: dict[str, float] = defaultdict(float)
    for term, code in words:
        ids = by_name.get(term, ())
        common = len(ids) > GENERIC
        for nid in ids:
            n = g.nodes[nid]
            unit = g.unit.get(nid, nid)
            member = unit != nid
            if member and n["name"].lower() == g.nodes[unit]["name"].lower():
                continue          # a constructor: its class holds it
            if code and not common:
                score[nid] += 13
            elif n["kind"] in ("field", "property", "variable"):
                continue          # a plain word names a field too often to be worth reading
            elif (member or common) and not fits(nid):
                continue          # "create", "upload": every class has one; only the ones the question is about
            else:
                score[nid] += 10
        if code and len(term) >= 5:
            for name, ids2 in by_name.items():
                if name != term and (name.startswith(term) or (len(term) >= 6 and term in name)):
                    for nid in ids2:
                        if g.nodes[nid]["kind"] not in ("field", "property", "variable"):
                            score[nid] += 4
    files_named = {t for t, code in words if code and "." in t}
    pointed = {str(m.get("file") or m.get("value") or "") for m in mentions or []} | ({open_file} if open_file else set())
    if selection and selection.get("path"):
        pointed.add(str(selection["path"]))
    for nid, n in g.nodes.items():
        if n["kind"] in SKIP_KINDS:
            continue
        f = n["file"] or ""
        base = f.rsplit("/", 1)[-1].lower()
        if base in files_named or f in pointed:
            # the file the person named or points at: its top-level parts
            if g.unit.get(nid) == nid:
                score[nid] += 6
    for m in mentions or []:
        if m.get("kind") == "symbol" and m.get("value"):
            for nid in by_name.get(str(m["value"]).split(".")[-1].lower(), ()):
                score[nid] += 8
    ranked = []
    for nid, s in score.items():
        n = g.nodes[nid]
        s += KIND_BONUS.get(n["kind"], 0)
        if _test_path(n["file"] or "") and not about_tests and (n["file"] or "") not in pointed:
            continue        # tests only when the question is about tests, or the person points at one
        if s > 6:
            ranked.append((-s, n["file"] or "", n["line"] or 0, nid))
    if not ranked:
        return ""
    ranked.sort()
    chosen: list[str] = []
    per_file: dict[str, int] = defaultdict(int)
    for _, f, _, nid in ranked:
        if per_file[f] >= PER_FILE:
            continue
        # a member under a chosen unit adds little unless it matched by itself (a method of a long class: its own range)
        if score[nid] < 10 and any(g.unit.get(nid) == c and c != nid for c in chosen):
            continue
        chosen.append(nid)
        per_file[f] += 1
        if len(chosen) >= LIMIT:
            break
    users: dict[str, list[str]] = defaultdict(list)
    wanted = set(chosen)
    for s, t, _, line in g.uses:
        if t in wanted and len(users[t]) < USERS and g.unit.get(s) != g.unit.get(t) \
                and (about_tests or not _test_path(g.nodes[s]["file"] or "")):
            label = f"{codegraph_view._short(g, s)} ({g.nodes[s]['file']}:{line or g.nodes[s]['line']})"
            if label not in users[t]:
                users[t].append(label)
    # what the chosen methods call, so a question that follows a path ("down to the database") has the next steps
    calls: dict[str, list[str]] = defaultdict(list)
    for s, t, kind, line in g.uses:
        if s in wanted and kind == "calls" and len(calls[s]) < CALLS and g.unit.get(s) != g.unit.get(t) \
                and g.nodes[s]["kind"] in ("method", "function", "constructor") and not _test_path(g.nodes[t]["file"] or ""):
            label = f"{codegraph_view._short(g, t)} ({g.nodes[t]['file']}:{g.nodes[t]['line']})"
            if label not in calls[s]:
                calls[s].append(label)
    lines = ["Where to look (keel looked these up in the project's code graph for this question; start here):"]
    for nid in chosen:
        n = g.nodes[nid]
        rng = f"{n['line']}-{n['end_line']}" if n.get("end_line") and n["end_line"] != n["line"] else str(n["line"])
        sig = " ".join((n.get("signature") or "").split())
        sig = f" {sig[:70]}{'…' if len(sig) > 70 else ''}" if sig and len(sig) > len(n["name"]) else ""
        used = f" — used by {', '.join(users[nid])}" if users.get(nid) else ""
        then = f" — calls {', '.join(calls[nid])}" if calls.get(nid) else ""
        lines.append(f"- `{n['file']}:{rng}` {n['kind']} {codegraph_view._short(g, nid)}{sig}{used}{then}")
    lines.append("Read the ranges you need together, in one turn (several Read calls with offset and limit at once): "
                 "every turn sends the whole conversation again. Do not list or search the project for names that are "
                 "here. Look further only when these are not enough.")
    out = "\n".join(lines)
    while len(out) > MAX_CHARS and len(lines) > 3:
        lines.pop(-2)
        out = "\n".join(lines)
    return out
