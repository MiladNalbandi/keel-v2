"""The fake model: deterministic, no network.

Each agent gets canned output that looks like real work: a short note, one MCP tool call (keel
keel_next, simulated), a real read through the guarded tools, and for writing agents one real file
write with its diff. Tokens follow the agent's typical size, so budgets and estimates behave.

Model id "fake-rogue" also writes a test file directly to disk, bypassing the tools, the way a CLI
agent could. The diff guard must catch it; tests use this.

Model id "fake-lab" (v0.17.4 real checks) acts like a real run that looped: the plan writes the build order only into
the spec's Plan ("**ORDER**: AC-2, AC-1", no ORDER marker), the test author answers AMEND on AC-1, and the amendment
explorer says it is not a spec defect and writes nothing.

KEEL_FAKE_COMMAND: a shell command every writing agent runs through its tools first (run_command), so a test or a
real check can watch keel's rules refuse it (v0.17.2 rule packs).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

from .. import config, layout
from ..tools import git
from . import catalog
from .base import AgentRequest, AgentResult, Emit

SECTIONS = ["architecture", "domain", "conventions", "data", "integrations"]
LENSES = ["correctness", "security", "performance", "architecture"]


def key_of(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_") or "item"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:48] or "work"


SOURCE = re.compile(r"\.(py|java|kt|kts|groovy|scala|ts|tsx|js|jsx|mjs|go|rs|rb|cs|php|swift|c|cc|cpp|h|hpp)$")
CITABLE = re.compile(r"[A-Za-z0-9_@./-]+")     # the characters a knowledge citation may use (runtime/knowledge.py)


def first_source(root: str | None) -> str:
    """The file the fake librarian cites: the first source file the project tracks (`git ls-files`). keel's own demo
    path would not exist in another project (a Java one), and the knowledge check refuses a citation to a missing file."""
    files = [f for f in (git.tracked(root) if root else []) if CITABLE.fullmatch(f)]
    return next((f for f in files if SOURCE.search(f)), files[0] if files else "src/scores/__init__.py")


def default_acs(title: str) -> list[dict]:
    return [
        {"id": "AC-1", "layer": "API", "title": f"{title}: the main case works"},
        {"id": "AC-2", "layer": "API", "title": f"{title}: bad input is refused with a clear message"},
    ]


# keel's spec starts with these six sections (content/templates/spec.md, 0.18.25); the fake spec changes no payload
# and no table, so both say "none" with the reason
TOP_SECTIONS = [
    "## In plain words", "",
    "Today players cannot see where they stand. We will show each player's rank next to the score. A player will see "
    "a number like 3 beside their name, the way a race shows who came third. Scores and names stay as they are. We still "
    "need to find out how ties should look.", "",
    "## For engineers", "",
    "- Inbound: unchanged; run() in src/scores/__init__.py:1 is the entry point.",
    "- Domain: a new rank() orders players by score, ties share a rank.",
    "- Persistence: none; ranks are computed on read, so there is no ordering or idempotency concern.",
    "- Compatibility: callers that ignore the new field keep working; no messages are queued.",
    "- Failure: bad input raises ValueError; the caller logs it.", "",
    "## What will change", "",
    "- src/scores/__init__.py: changed, run() returns the rank too",
    "- tests/: new, one test per criterion", "",
    "## Payload changes", "",
    "none: run() is a library call; no request, response or message changes shape.", "",
    "## Database changes", "",
    "none: ranks are computed in memory; no table or data changes.", "",
    "## Architecture", "",
    "```", "[caller] --> [run() ~changed] --> [rank() +new] --> (no persistence, unchanged)", "```", "",
    "It stays inside the scores package, like run() at src/scores/__init__.py:1.", "",
]


def spec_text(title: str, acs: list[dict]) -> str:
    """A spec in keel's template (content/templates/spec.md): every section keel's spec check asks for."""
    lines = [f"# {title}", "", *TOP_SECTIONS, "## Goal", "", f"{title}, so that players see where they stand.", "",
             "## User stories", "", "- As a player, I want to see my rank, so that I know where I stand.", "",
             "## Findings (evidence)", "", "- The scores live in `src/scores/__init__.py:1`; no test covers ranks yet.", "",
             "## Acceptance criteria", ""]
    lines += [f"- **{a['id']}** [{a.get('layer', 'API')}] {a['title']}" for a in acs]
    lines += ["", "## Test layers", ""] + [f"- {a['id']}: unit; e2e: not needed, no browser path" for a in acs]
    lines += ["", "## Validation and security rules", "", "- No input from outside; nothing is logged.", "",
              "## Edge cases", "", "- No scores: an empty list.", "",
              "## Risks and cost", "", "- One module, no migration, no deploy order.", "",
              "## Out of scope", "", "- Paging.", "",
              "## Definition of done", "", "- [ ] Every criterion is green on its layers.", "- [ ] The review passed.", "",
              "## Plan", "", "1. Write the failing test for each criterion.", "2. Make it pass with the least code.", ""]
    return "\n".join(lines)


CLARIFY_STEPS = ("read the request and the code", "clarify questions", "assemble the outputs")
CLARIFY_TYPES = (("bug", r"bug|broken|crash|error|fails"), ("performance", r"slow|latency|performance"),
                 ("refactor", r"refactor|restructure"), ("api", r"\bapi\b|integrat|webhook"),
                 ("architecture", r"architecture|design decision"))


def clarify_answer(req: AgentRequest) -> str:
    """The clarify flow's explorers (content/workflows/clarify.yaml): the read step names a type from the title and the
    request's words; the rounds ask one round of two questions, then NO_QUESTIONS; assemble writes a spec that keel's spec
    check passes (with What will change, Payload changes and Architecture), the red flags, the next step and the prompt."""
    asked = (req.title or "") + " " + (req.prompt or "").split("What the user asked for:\n", 1)[-1].split("\n", 1)[0] \
        if "What the user asked for:" in (req.prompt or "") else (req.title or "")
    if req.step_name == "read the request and the code":
        kind = next((k for k, words in CLARIFY_TYPES if re.search(words, asked, re.I)), "feature")
        return ("- the request touches src/scores/__init__.py:1 (run)\n- the pattern to follow: src/scores/__init__.py:1\n"
                f"- tests: tests/ (pytest)\nTASK-TYPE: {kind} — the request's words\nSECTIONS: 1, 2, 3, 5, 6")
    if req.step_name == "clarify questions":
        if (req.feedback or "").startswith("Answers to your questions:"):
            return "- what: answered\n- where: src/scores/__init__.py:1\nNO_QUESTIONS: the chosen sections are answered"
        qs = [{"id": "where", "question": "Which module does this live in?", "why": "it sets the files the criteria name",
               "options": [{"label": "src/scores", "description": "next to run() at src/scores/__init__.py:1", "recommended": True},
                           {"label": "a new module", "description": "a new package beside scores"}]},
              {"id": "constraints", "question": "What must not change?", "why": "it bounds the change",
               "options": [{"label": "run()'s return shape", "description": "callers read it (src/scores/__init__.py:1)",
                            "recommended": True}, {"label": "nothing in particular", "description": "free to change"}]}]
        return "- what: the request says it\n- where: open\n```keel-questions\n" + json.dumps(qs) + "\n```"
    title = req.title or "the change"
    # a request that stores something gets a database change (a new nullable column and an index), else "none"
    stores = bool(re.search(r"\b(store|save|persist|column|table|database)\b", asked, re.I))
    db = ("## Database changes\n\n```\nscores before                      scores after\n"
          "  id      bigint  not null pk        id      bigint  not null pk\n"
          "  points  int     not null           points  int     not null\n"
          "                                   + rank    int     null\n"
          "                                   + index scores_rank_idx (rank)\n```\n\n"
          "Migration `V7__scores_rank`: up `ALTER TABLE scores ADD COLUMN rank int NULL; CREATE INDEX CONCURRENTLY "
          "scores_rank_idx ON scores (rank);` down `DROP INDEX scores_rank_idx; ALTER TABLE scores DROP COLUMN rank;`. "
          "Online on PostgreSQL (a nullable column, the index built concurrently); about 10 000 rows.\n"
          "Backfill: none, rank stays null until the next run (no cost).\n"
          "Deploy order: migration, then consumer, then producer.\n"
          "Rollback: the down SQL; it loses the stored ranks only (they are computed again).\n"
          "Repository: ScoreRepository.save writes rank; ScoreRepository.top reads it.\n\n") if stores else \
         "## Database changes\n\nnone: ranks are computed in memory; no table or data changes.\n\n"
    return ("## In plain words\n\nToday players cannot see where they stand. We will show each player's rank. "
            "A player will see a number like 3 next to their name, like the third runner in a race. Scores and names "
            "stay the same. We still need to find out how ties should look.\n\n"
            "## For engineers\n\n- Inbound: run() in src/scores/__init__.py:1 is unchanged as the entry point.\n"
            "- Domain: a new rank() orders by points; ties share a rank, so it is deterministic and idempotent.\n"
            f"- Persistence: {'ScoreRepository writes the nullable rank column in the same transaction' if stores else 'none'}.\n"
            "- Compatibility: old callers ignore the new count field; nothing is queued, so a rolling deploy is safe.\n"
            "- Failure: bad input raises ValueError, logged by the caller; no new metric.\n\n"
            "## What will change\n\n- src/scores/__init__.py: changed, run() gets the new case\n"
            "- tests/test_scores.py: new, a test per criterion\n\n"
            "## Payload changes\n\nrun() returns:\n\n```json\n// before\n{\"ok\": true}\n```\n\n```json\n// after\n"
            "{\"ok\": true, \"count\": 3}   // + count\n```\n\nCompatible: old callers ignore count; nothing is queued.\n\n"
            + db +
            "## Architecture\n\n```\n[caller] -> [run() ~changed] -> [rank() +new] -> "
            + ("[ScoreRepository ~changed]" if stores else "(no persistence) [unchanged]") + "\n```\n\n"
            "It stays in the scores package, like run() at src/scores/__init__.py:1.\n\n"
            f"## Goal\n\n{title}, so that players see where they stand.\n\n"
            "## User stories\n\n- As a player, I want to see my rank, so that I know where I stand.\n\n"
            "## Decisions\n\n- Which module (asked): src/scores.\n\n"
            "## Findings (evidence)\n\n- run() lives in src/scores/__init__.py:1; no test covers it yet.\n\n"
            "## Acceptance criteria\n\n"
            f"- **AC-1** [API] Given scores, when {title} runs, then the main case works\n"
            "- **AC-2** [API] Given bad input, when it runs, then it is refused with a clear message\n\n"
            "## Test layers\n\n- AC-1: unit; e2e: not needed, no browser path\n- AC-2: unit; e2e: not needed\n\n"
            "## Request path\n\n```\nrun(scores)                     ~ changed\n  -> rank()                      + new\n```\n\n"
            "## Data and migrations\n\n" + ("see Database changes" if stores else "none") + "\n\n"
            "## Validation and security rules\n\n- Bad input is refused; nothing is logged.\n\n"
            "## Edge cases\n\n- No scores: an empty list.\n\n## Risks and cost\n\n- One module"
            + (", one online migration." if stores else ", no migration.") + "\n\n"
            "## Assumptions\n\n- Ties share a rank.\n\n## Unverified and open questions\n\nnone\n\n"
            "## Out of scope\n\n- Paging.\n\n"
            "## Red-flag check\n\nnone\n\n## Next step\n\nThe feature flow, with these criteria.\n\n"
            f"## Assembled prompt\n\n````prompt\nI need to implement: {title}\n\nLocation: src/scores  |  Stack: Python\n"
            "Follow the pattern from: src/scores/__init__.py:1\n\nRequirements:\n- the main case works\n\nConstraints:\n"
            "- run()'s return shape stays\n\n---\nBefore writing any code:\n1. Ask me if anything is unclear\n````\n")


def _plan(req: AgentRequest) -> tuple[str | None, str, str, dict]:
    """(path to write or None, file content, final answer, structured data). A step that collects a list gets one."""
    path, content, answer, data = _canned(req)
    want = re.search(r"JSON list of ([\w-]+)", req.prompt or "")
    if want and "```json" not in answer:
        items = [{"title": f"{want.group(1)} {n}: {t}"} for n, t in enumerate(["a stale cache", "an off-by-one", "a race"], 1)]
        answer += "\n```json\n" + json.dumps(items) + "\n```"
    return path, content, answer, data


def _canned(req: AgentRequest) -> tuple[str | None, str, str, dict]:
    from .. import addons

    got = addons.fake_answer(req)       # an add-on's own agents (keel_engine/addons.py)
    if got:
        return got
    agent, phase, title = req.agent, req.phase, req.title or "the change"
    ac = req.ac or {}
    ac_id = ac.get("id") or "BUG-1"
    k = key_of(ac_id)
    if agent == "explorer" and req.item:
        return None, "", f"MAP ({req.item.get('id')}): src/scores/__init__.py:1 is the pattern; tests under tests/. MAP-END", {}
    specs = layout.resolve(req.root).specs if req.root else "docs/specs"   # where the project's layout keeps specs
    lab = req.model.get("model") == "fake-lab"
    if lab and agent == "explorer" and req.step_name == "spec amendment":
        return None, "", ("No amendment: this is not a spec defect. AC-1 needs what another criterion builds first; "
                          "it is an order question.\nREOPEN: none\nCONTRACT: no"), {}
    if lab and agent == "test-author" and ac_id == "AC-1":
        return None, "", "There is no seam to attach a compiling test yet.\nAMEND: AC-1 needs the ranking table AC-2 builds", {}
    if agent == "explorer" and phase == "spec" and req.step_name == "spec amendment":
        path = f"{specs}/{slug(title)}.md"
        old = (Path(req.root) / path).read_text() if (Path(req.root) / path).is_file() else spec_text(title, req.acs or [])
        block = ("\n## Amendments\n\n### 2026-01-01 — AC-1\nWas: the main case works.\nNow: the main case works for ties too.\n"
                 "Why: the test showed ties.\n")
        return path, old.rstrip() + "\n" + block, "Amendment written.\nREOPEN: none\nCONTRACT: no", {}
    if agent == "explorer" and req.step_name.startswith("outside docs"):
        # 0.18.21 feature's web_check: outside docs are needed when the request names a vendor, a webhook, an SDK or a
        # third-party API (the step's own instructions name them too, so only the title and the request count)
        asked = title + " " + (req.prompt or "").split("What the user asked for:\n", 1)[-1].split("\n", 1)[0] \
            if "What the user asked for:" in (req.prompt or "") else title
        if re.search(r"webhook|vendor|third-party|\bsdk\b", asked, re.I):
            return None, "", "Needs from outside: the payload fields of the webhook the request names.\nWEB: yes", {}
        return None, "", "Nothing from outside: the work stays inside this code.\nWEB: no", {}
    if agent == "explorer" and phase == "spec" and req.step_name in CLARIFY_STEPS:
        return None, "", clarify_answer(req), {}
    if agent == "explorer" and phase == "spec" and not req.acs and "Write the spec under" not in (req.prompt or ""):
        # an explorer in the spec phase that does not write the spec (prompts.spec_writer): the feature's interview (the
        # request answers every lens), the spike's frame
        if req.step_name == "interview":
            return None, "", ("- goal and users: the request says it\n- scope: the request names it\n"
                              "NO_QUESTIONS: the request answers every lens that changes the criteria"), {}
        # 0.18.23 the frame is about the decision (need, for whom, what evidence decides, limits, options); how to
        # build it goes to the spec as open questions
        framed = (f"Framed: {title}.\nDecision: whether to build it, and which option.\n"
                  "For whom: the request says it.\nWhat decides it: the evidence the angles collect.\n"
                  "Limits: none given.\nOpen questions for the spec: none yet.\n"
                  "Nothing from outside this repository is needed.")
        if req.step_name == "frame the question":      # the spike's two angles (they disagree: SPIKE_ANGLES)
            framed += "\n```json\n" + json.dumps([{"title": t} for t in SPIKE_ANGLES]) + "\n```"
        return None, "", framed, {}
    if agent == "explorer" and phase in ("spec", "triage"):
        acs = req.acs or default_acs(title)
        path = f"{specs}/{slug(title)}.md"
        answer = f"Spec written with {len(acs)} acceptance criteria."
        if req.step_name.startswith("plan"):
            answer = "Plan written under the criteria.\nORDER: " + ", ".join(a["id"] for a in acs)
            if lab:     # the order only in the spec's Plan, last criterion first
                text = spec_text(title, acs).replace("## Plan\n", "## Plan\n\n**ORDER**: " + ", ".join(a["id"] for a in reversed(acs)) + "\n")
                return path, text, "Plan written under the criteria.", {"acs": acs, "spec": path}
        return path, spec_text(title, acs), answer, {"acs": acs, "spec": path}
    if agent == "explorer":
        return None, "", "MAP: Python package with pytest tests under tests/. MAP-END", {}
    if agent == "contract-author":
        return f"docs/contract/{slug(title)}.md", f"# Contract: {title}\n\nNo HTTP surface changes; library functions only.\n", \
            "Contract notes written.", {}
    if agent == "test-author":
        body = (f'"""{ac_id}: {ac.get("title", "")}"""\nfrom scores.{k} import run\n\n\n'
                f"def test_{k}_works():\n    assert run() == \"{ac_id} done\"\n")
        return f"tests/test_{k}.py", body, f"RED: tests/test_{k}.py asserts {ac_id}.", {}
    if agent == "reproducer":
        # named after the flow's bug id (BUG-<n>, bug_intake), so keel can run it alone
        bug = next((a["id"] for a in req.acs or [] if str(a.get("id", "")).startswith("BUG-")), "BUG-1")
        name = f"{key_of(bug)}_{key_of(title)}"
        body = (f'"""{bug} {title}: reproduces the bug."""\nfrom scores.fix_{key_of(title)} import run\n\n\n'
                f"def test_{name}():\n    assert run() == \"fixed\"\n")
        return f"tests/test_bug_{key_of(title)}.py", body, "The test fails for the reported reason.\nLAYER: API\nREPRO: confirmed", {}
    if agent == "implementer" and phase == "integration":
        return None, "", "Nothing to wire: the criteria already call the real code.", {}
    if agent == "e2e-author" and phase == "smoke":
        ids = " ".join(dict.fromkeys(re.findall(r"\b(AC-\d+) \[SMOKE\]", req.prompt or "")))
        return f"smoke/{slug(title)}.sh", "#!/usr/bin/env bash\n" + (f"# {ids}\n" if ids else "") + "set -e\necho smoke ok\n", \
            "Smoke check written.", {}
    if agent == "implementer" and phase == "lint-fix":
        return "src/scores/lint_fixed.py", "# lint: fixed\n", "Fixed what the static checks reported; behaviour unchanged.", {}
    if agent == "implementer" and phase == "bug-fix":
        return f"src/scores/fix_{key_of(title)}.py", 'def run():\n    return "fixed"\n', "Fixed at the root cause.", {}
    if agent == "implementer" and phase == "refactor":
        return None, "", "Nothing worth tidying: the green code is already small.\nREFACTOR: none", {}
    if agent == "implementer" and phase == "close":
        return None, "", "Nothing in the docs is wrong for this change: no edit.", {}
    if agent == "implementer":
        return f"src/scores/{k}.py", f'def run():\n    return "{ac_id} done"\n', f"GREEN: {ac_id} passes.", {}
    if agent == "e2e-author":
        # the criterion ids in the test, as the step asks (the trace finds an [E2E] criterion's test by its id)
        ids = " ".join(dict.fromkeys(re.findall(r"\b(AC-\d+) \[E2E\]", req.prompt or "")))
        return f"e2e/test_{key_of(title)}_e2e.py", f'"""E2E for {title}.{" " + ids if ids else ""}"""\n\n\ndef test_flow():\n    assert True\n', \
            "E2E spec written and passing.\nE2E-RESULT: pass", {}
    if agent == "librarian":
        section = req.section or (SECTIONS[req.index % len(SECTIONS)] if phase in ("setup", "memory") else "architecture")
        kdir = layout.resolve(req.root).knowledge if req.root else "docs/knowledge"
        cite = first_source(req.root)
        return f"{kdir}/{section}.md", f"# {section.title()}\n\nThe code starts in `{cite}:1`.\n", \
            f"Knowledge section {section} written with citations.", {}
    if agent == "criterion-reviewer":
        return None, "", f"AC-REVIEW: PASS — the test proves {ac_id}.", {"verdict": "pass"}
    if agent == "lens-reviewer":
        lens = LENSES[req.index % len(LENSES)]
        return None, "", f"REVIEW ({lens}): no findings.\nBLOCKING: no", {"verdict": "pass", "lens": lens}
    if agent == "branch-reviewer":
        return None, "", "No findings across the branch diff.\nCODE-REVIEW: pass", {"verdict": "pass"}
    if agent == "security-auditor":
        return None, "", "SECURITY: no findings.", {"verdict": "pass"}
    if agent == "helper" and (phase not in ("none", "") or getattr(req.toolbox, "confine", False)):
        # Fix mode and side sessions: one real change through the guarded tools, so Undo, Done and Keep have work
        return "src/scores/helper_fix.py", 'def helped():\n    return "fixed by KeelBot"\n', \
            "Changed src/scores/helper_fix.py: a small helper the gate asked for.", {}
    if agent == "helper":
        asked = (req.prompt or "").split("Question:", 1)[-1].strip().splitlines()[0][:120] if "Question:" in (req.prompt or "") \
            else (req.prompt or "").strip().splitlines()[-1][:120]
        turns = len(req.history) // 2 + 1
        return None, "", (f"You asked: {asked}\nThe package starts in `src/scores/__init__.py:1`. This is answer {turns} "
                          "of this session.\n\n## Where to look\n\n- **entry**: `run()` in src/scores/__init__.py:1\n"
                          "- the tests: tests/test_scores.py:3\n\n```python\ndef run(players: list[str]) -> dict:\n"
                          "    # rank the players, best first\n    return {\"ok\": True, \"count\": len(players)}\n```"), {}
    if agent == "web-researcher":
        # 0.18.20 the spike's web_docs step: sources with URLs, what was tried, what stays open
        return None, "", ("CONFIRMED\n- The event carries `estimated_delivery` (ISO 8601): https://docs.example.com/webhooks "
                          "\"estimated_delivery: the latest ETA\"\n\n| tried | result |\n|---|---|\n"
                          "| search: example webhook eta | 3 hits |\n| https://docs.example.com/webhooks | found |\n\n"
                          "Unconfirmed: whether it can be null; a sample payload from the sandbox would settle it.\n"
                          "CONFIDENCE: medium"), {}
    if agent == "finding-verifier":
        # review-pr's check step: every claim of its batch confirmed, as the reviewer rated it
        claims = (req.item or {}).get("claims") or []
        lines = [f"CHECK | {c['id']} | confirmed | the lines show it | sev:{c.get('severity') or 'minor'} | conf:high"
                 for c in claims]
        return None, "", "\n".join(lines + ["TICKET-REVIEW: done"]), {}
    if agent in ("pr-reviewer", "change-reviewer"):
        # review-pr: result lines for its job (every item met, one minor finding, one quality note), or a confirmed check
        item = req.item or {}
        if item.get("job") == "check":
            return None, "", "CHECK | confirmed | the lines say what the claim says\nTICKET-REVIEW: done", {}
        if item.get("job") == "settle":
            where = (item.get("files") or ["src/scores/__init__.py"])[0]
            return None, "", f"CHECK | met | {where}:1 | read the code: it does it\nTICKET-REVIEW: done", {}
        files = [str(f).split(" ")[0] for f in item.get("files") or [] if not str(f).startswith("…")] or ["src/scores/__init__.py"]
        lines = [f"READ | {f}" for f in files]    # keel 0.18.57: a reviewer names the files it read
        lines += [f"{it['id']} | met | {(it.get('files') or files)[0]}:1 | the change does it" for it in item.get("items") or []]
        if item.get("job") in ("all", "bugs"):
            lines.append(f"F | minor | {files[0]}:1 | the new name hides what it counts | name it after what it counts")
        if item.get("job") in ("all", "quality"):
            lines.append(f"Q | {files[0]}:1 | like its neighbour | no docstring here | add one line, like the others")
        lines += ["S | A small change that does what it says.", "TICKET-REVIEW: done"]
        return None, "", "\n".join(lines), {}
    if agent == "investigator" and req.step_name == "one researcher per angle":
        # 0.18.23 the spike's angles: the working (tool-call log, narration) first, then the clean ANGLE REPORT
        return None, "", SPIKE_ANGLES.get(str((req.item or {}).get("title")), next(iter(SPIKE_ANGLES.values()))), {}
    if agent == "investigator" and req.step_name.startswith("findings"):
        return None, "", SPIKE_FINDINGS, {}
    if agent == "investigator":
        return None, "", ("ROOT CAUSE: the counter is read before it is written (src/scores/__init__.py:1).\n"
                          "ROOT-CAUSE: confirmed"), {}
    return None, "", f"{agent}: done.", {}


# 0.18.23 the spike's two angles, as a real CLI agent answers: its working first, then the ANGLE REPORT. They disagree
# on where the ETA is stored and whether it needs a migration (a real spike's angles did, and nobody said so).
_WORKING = ("I'll start with the webhook handler.\n● cd src; cat -n webhook/EtaHandler.kt\n│ 249  fun onEvent(e: Event) {\n"
            "└ 220 lines…\n/ Search (grep) estimated_delivery\nNow the entity.\n")
SPIKE_ANGLES = {
    "reuse the subscription column": _WORKING + (
        "ANGLE REPORT\nAnswer: the ETA fits the column that exists.\nFacts:\n"
        "- where the ETA is stored: tracking_subscriptions.estimated_delivery_date — src/db/TrackingSubscription.kt:12\n"
        "- migration needed: no — src/db/migrations/V12__subscriptions.sql:4\n"
        "Unverified:\n- whether the vendor sends it on every update\nRisks:\n- one value, no history\n"
        "Cost: 2 files, no migration, S\nVERDICT: promising"),
    "a column on trackings": _WORKING + (
        "ANGLE REPORT\nAnswer: the ETA needs its own column on trackings.\nFacts:\n"
        "- where the ETA is stored: trackings.estimated_delivery_date — src/db/Tracking.kt:30\n"
        "- migration needed: yes — a new V13 migration\n"
        "Unverified:\n- none\nRisks:\n- a migration on a large table\n"
        "Cost: 4 files, 1 migration, M\nVERDICT: possible"),
}
SPIKE_FINDINGS = (
    "DECISION: build it on the existing tracking_subscriptions column.\n"
    "BEFORE BUILDING:\n1. Does the vendor send the ETA on every update?\n"
    "CONFLICTS: found\n"
    "- where the ETA is stored: \"reuse the subscription column\" says tracking_subscriptions "
    "(src/db/TrackingSubscription.kt:12); \"a column on trackings\" says trackings (src/db/Tracking.kt:30); the evidence "
    "supports tracking_subscriptions: the column exists (src/db/migrations/V12__subscriptions.sql:4)\n"
    "- migration needed: one angle says no, the other a new V13; no migration is needed, the column exists\n"
    "COST: 2 files, no migration, small\nNEXT: spec\n\n"
    "## Findings (evidence)\n- the column exists: src/db/migrations/V12__subscriptions.sql:4\n\n"
    "RECOMMEND: reuse the subscription column")


# 0.18.23 what the fake KeelBot thinks before it answers (thinking deltas, never part of the answer)
FAKE_THINKING = "The person asks: {question}. I look at the package entry first, then answer with file:line."


def chunks(text: str, size: int = 12) -> list[str]:
    """The text in pieces of about `size` characters, cut after a space where one is near (as a model's tokens come)."""
    out, i = [], 0
    while i < len(text):
        j = min(len(text), i + size)
        sp = text.rfind(" ", i + 1, j + 4) if j < len(text) else -1
        if sp > i:
            j = sp + 1
        out.append(text[i:j])
        i = j
    return out


class FakeRunner:
    async def run(self, req: AgentRequest, emit: Emit) -> AgentResult:
        delay = config.fake_delay()

        async def pause():
            if delay:
                await asyncio.sleep(delay)

        if req.prompt.strip() == "Reply with exactly: OK":
            return AgentResult(text="OK", tokens_in=12, tokens_out=1)

        target = (req.ac or {}).get("id") or req.title or req.phase
        emit("text", f"{req.agent}: working on {target} in phase {req.phase}.")
        if catalog.effort_for(req.model):        # recorded, so tests (and a try in the dashboard) see the effort arrive
            emit("thinking", f"Thinking effort: {catalog.effort_for(req.model)}.")
        await pause()
        if req.feedback:
            emit("thinking", f"Taking the feedback into account: {req.feedback[:200]}")
        emit("tool", "{}", tool="keel_next", server="keel", output=f"next step is {req.phase}", ok=True, ms=14)
        await pause()
        for candidate in ("README.md", "pyproject.toml"):
            if (Path(req.root) / candidate).is_file():
                out = req.toolbox.read_file(candidate)
                emit("read", "\n".join(out.splitlines()[:400]), path=candidate, ok=not out.startswith("REFUSED"), ms=2)
                break
        await pause()

        path, content, answer, data = _plan(req)
        command = os.environ.get("KEEL_FAKE_COMMAND", "").strip()
        if path and command:
            out = req.toolbox.run_command(command, timeout=60)
            refused = out.startswith("REFUSED")
            emit("guard" if refused else "tool", out if refused else command, tool="run_command",
                 **({} if refused else {"output": out[:2000]}), ok=not refused)
            await pause()
        if path:
            msg = req.toolbox.write_file(path, content)
            if msg.startswith("REFUSED"):
                emit("guard", msg, tool="write_file", path=path, ok=False)
            else:
                w = req.toolbox.writes[-1]
                emit("write" if w["new"] else "edit", f"{'Write' if w['new'] else 'Edit'} {path}", path=path, diff=w["diff"], ok=True)
            await pause()

        if req.model.get("model") == "fake-rogue":
            rogue = Path(req.root) / "tests" / f"test_rogue_{key_of(req.agent)}.py"
            rogue.parent.mkdir(parents=True, exist_ok=True)
            rogue.write_text("def test_rogue():\n    assert True  # written behind keel's back\n")
            emit("text", f"(rogue) wrote {rogue.relative_to(req.root)} directly.")

        # the answer as a model writes it: in pieces (KEEL_FAKE_STREAM_DELAY apart), then the whole answer
        stream_delay = config.fake_stream_delay()
        if req.agent == "helper":
            # 0.18.23 KeelBot's thinking first, as Claude streams it (thinking deltas, then the whole block), apart
            # from the answer
            thought = FAKE_THINKING.format(question=(req.prompt or "").rsplit("Question:", 1)[-1].strip()[:80])
            for chunk in chunks(thought):
                emit("thinking_delta", chunk)
                if stream_delay:
                    await asyncio.sleep(stream_delay)
            emit("thinking", thought)
        for chunk in chunks(answer):
            emit("delta", chunk)
            if stream_delay:
                await asyncio.sleep(stream_delay)
        emit("answer", answer)
        k_in, k_out, _ = catalog.AGENT_DEFAULTS.get(req.agent, catalog.FALLBACK_AGENT)
        return AgentResult(text=answer, tokens_in=int(k_in * 1000) + 100 * req.index, tokens_out=int(k_out * 1000), data=data)
