"""The fake model: deterministic, no network.

Each agent gets canned output that looks like real work: a short note, one MCP tool call (keel
keel_next, simulated), a real read through the guarded tools, and for writing agents one real file
write with its diff. Tokens follow the agent's typical size, so budgets and estimates behave.

Model id "fake-rogue" also writes a test file directly to disk, bypassing the tools, the way a CLI
agent could. The diff guard must catch it; tests use this.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from .. import config
from . import catalog
from .base import AgentRequest, AgentResult, Emit

SECTIONS = ["architecture", "domain", "conventions", "data", "integrations"]
LENSES = ["correctness", "security", "performance", "architecture"]


def key_of(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_") or "item"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:48] or "work"


def default_acs(title: str) -> list[dict]:
    return [
        {"id": "AC-1", "layer": "API", "title": f"{title}: the main case works"},
        {"id": "AC-2", "layer": "API", "title": f"{title}: bad input is refused with a clear message"},
    ]


def spec_text(title: str, acs: list[dict]) -> str:
    lines = [f"# {title}", "", "## Acceptance criteria", ""]
    lines += [f"- **{a['id']}** [{a.get('layer', 'API')}] {a['title']}" for a in acs]
    lines += ["", "## Plan", "", "1. Write the failing test for each criterion.", "2. Make it pass with the least code.", ""]
    return "\n".join(lines)


def _plan(req: AgentRequest) -> tuple[str | None, str, str, dict]:
    """(path to write or None, file content, final answer, structured data). A step that collects a list gets one."""
    path, content, answer, data = _canned(req)
    want = re.search(r"JSON list of ([\w-]+)", req.prompt or "")
    if want and "```json" not in answer:
        items = [{"title": f"{want.group(1)} {n}: {t}"} for n, t in enumerate(["a stale cache", "an off-by-one", "a race"], 1)]
        answer += "\n```json\n" + json.dumps(items) + "\n```"
    return path, content, answer, data


def _canned(req: AgentRequest) -> tuple[str | None, str, str, dict]:
    agent, phase, title = req.agent, req.phase, req.title or "the change"
    ac = req.ac or {}
    ac_id = ac.get("id") or "BUG-1"
    k = key_of(ac_id)
    if agent == "explorer" and phase in ("spec", "triage"):
        acs = req.acs or default_acs(title)
        path = f"docs/specs/{slug(title)}.md"
        return path, spec_text(title, acs), f"Spec written with {len(acs)} acceptance criteria.", {"acs": acs, "spec": path}
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
        body = (f'"""{title}: reproduces the bug."""\nfrom scores.fix_{key_of(title)} import run\n\n\n'
                f"def test_bug_{key_of(title)}():\n    assert run() == \"fixed\"\n")
        return f"tests/test_bug_{key_of(title)}.py", body, "The test fails for the reported reason.\nREPRO: confirmed", {}
    if agent == "implementer" and phase == "bug-fix":
        return f"src/scores/fix_{key_of(title)}.py", 'def run():\n    return "fixed"\n', "Fixed at the root cause.", {}
    if agent == "implementer":
        return f"src/scores/{k}.py", f'def run():\n    return "{ac_id} done"\n', f"GREEN: {ac_id} passes.", {}
    if agent == "e2e-author":
        return f"e2e/test_{key_of(title)}_e2e.py", f'"""E2E for {title}."""\n\n\ndef test_flow():\n    assert True\n', \
            "E2E spec written and passing.", {}
    if agent == "librarian":
        section = req.section or (SECTIONS[req.index % len(SECTIONS)] if phase in ("setup", "memory") else "architecture")
        return f"docs/knowledge/{section}.md", f"# {section.title()}\n\nThe package lives in src/scores (`src/scores/__init__.py:1`).\n", \
            f"Knowledge section {section} written with citations.", {}
    if agent == "ac-reviewer":
        return None, "", f"AC-REVIEW: PASS — the test proves {ac_id}.", {"verdict": "pass"}
    if agent == "reviewer":
        lens = LENSES[req.index % len(LENSES)]
        return None, "", f"REVIEW ({lens}): no findings.\nBLOCKING: no", {"verdict": "pass", "lens": lens}
    if agent == "code-reviewer":
        return None, "", "No findings across the branch diff.\nCODE-REVIEW: pass", {"verdict": "pass"}
    if agent == "security-auditor":
        return None, "", "SECURITY: no findings.", {"verdict": "pass"}
    if agent == "investigator":
        return None, "", ("ROOT CAUSE: the counter is read before it is written (src/scores/__init__.py:1).\n"
                          "ROOT-CAUSE: confirmed"), {}
    return None, "", f"{agent}: done.", {}


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

        emit("answer", answer)
        k_in, k_out, _ = catalog.AGENT_DEFAULTS.get(req.agent, catalog.FALLBACK_AGENT)
        return AgentResult(text=answer, tokens_in=int(k_in * 1000) + 100 * req.index, tokens_out=int(k_out * 1000), data=data)
