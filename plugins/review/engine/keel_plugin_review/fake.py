"""The fake model's answers for the Code Review plugin's runs (moved from keel's models/fake.py with the plugin). The api's
ReviewAiService asks KeelBot in ask mode for an overview, findings, or the check of findings; each prompt asks for one
fenced JSON block (```keel-review-overview, ```keel-review-findings, ```keel-review-verify). The answers are the same
as keel 0.15.1's: e2e/review checks them."""

from __future__ import annotations

import json
import re


def answer(req):
    """KeelBot's answer to a review prompt, as keel's fake model gives every answer: (path, content, answer, data).
    None for anything else, and for KeelBot's fix mode and side sessions (keel's own fake answer is a file change
    there, and it comes first, as it always did)."""
    if req.agent != "helper" or "```keel-review-" not in (req.prompt or ""):
        return None
    if req.phase not in ("none", "") or getattr(req.toolbox, "confine", False):
        return None
    return None, "", review_answer(req.prompt or ""), {}


def review_answer(prompt: str) -> str:
    """The Code Review plugin's runs (api ReviewAiService): an overview, findings, or the check of findings, as the
    fenced JSON block each prompt asks for, built from the changed files the prompt lists."""
    files = re.findall(r"^  ([AMDRC])\S* (\S+) \(\+(\d+) \u2212(\d+)\)$", prompt, re.M)
    paths = [p for st, p, _a, _d in files if st != "D"] or ["README.md"]
    if "```keel-review-overview" in prompt:
        o = {"summary": f"This change touches {len(files)} file(s). It adds the new behaviour in {paths[0]} and its tests.",
             "files": [{"path": p, "what": f"{'adds' if st == 'A' else 'changes'} {p.rsplit('/', 1)[-1]}"} for st, p, _a, _d in files],
             "order": paths[:3], "diagram": f"caller \u2500\u25b6 {paths[0].rsplit('/', 1)[-1]} \u2500\u25b6 storage",
             "effort": 2, "risk": "medium", "risk_why": "it changes stored data", "split": "",
             "questions": ["What happens to existing users without a saved value?"]}
        return "Here is the overview.\n```keel-review-overview\n" + json.dumps(o) + "\n```"
    if "```keel-review-verify" in prompt:
        ids = re.findall(r"^- id (f_[0-9a-f]+):", prompt, re.M)
        verdicts = [{"id": i, "verdict": "confirmed" if n == 0 else "rejected",
                     "why": "line 1 shows it" if n == 0 else "the code handles this case"} for n, i in enumerate(ids)]
        return "I checked the claims.\n```keel-review-verify\n" + json.dumps({"verdicts": verdicts}) + "\n```"
    who = "A" if "You are reviewer A" in prompt else "B"
    findings = [{"title": f"value from {paths[0].rsplit('/', 1)[-1]} is never saved", "severity": "blocking",
                 "category": "correctness", "path": paths[0], "line": 1, "side": "RIGHT",
                 "why": "the new value is kept in memory only, so it is lost after a restart",
                 "suggestion": "", "fix": "save it through the repository", "pre_existing": False}] if who == "A" else [
        {"title": "no test for the upper limit", "severity": "should_fix", "category": "tests", "path": paths[-1], "line": 1,
         "side": "RIGHT", "why": "a value above the limit is not tested", "suggestion": "", "fix": "add a test", "pre_existing": False},
        {"title": "name could be clearer", "severity": "nit", "category": "design", "path": paths[0], "line": 1, "side": "RIGHT",
         "why": "", "suggestion": "", "fix": "", "pre_existing": False}]
    out = {"findings": findings, "security": "Checked auth and input on the changed code." if who == "A" else "",
           "tests": "" if who == "A" else "The new code has a test, one case is missing."}
    return f"Reviewer {who}: done.\n```keel-review-findings\n" + json.dumps(out) + "\n```"
