"""#101 a scripted model for flow tests. keel's harness runs every real model; this stands in for the model API behind it
(harness.providers.resolve), so a test drives a real flow with no key and no network, and sees what the fake CLIs on
PATH once showed: which agent ran, on which model, in which session, and what it was told.

    sm = scripted(monkeypatch)                          # every call answers "done"
    sm = scripted(monkeypatch, "- **AC-1** [API] ...")  # every call answers this text
    sm = scripted(monkeypatch, [turn1, turn2, ...])     # these turns in order, then "done"
    sm = scripted(monkeypatch, lambda req, n: turn)     # a function of the request and the call number

sm.runs  the AgentRequests the harness ran (agent, model, session, resume, prompt), in order
sm.calls the model turns: (the model dict, the key, the harness Request)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from keel_engine.harness import providers
from keel_engine.harness.providers.base import END, TOOL_USE, Request, Turn, Usage
from keel_engine.harness.runner import HarnessRunner


def answer(text: str, usage: tuple[int, int] = (10, 2), **more: int) -> Turn:
    return Turn(blocks=[{"type": "text", "text": text}], stop=END, usage=Usage(input=usage[0], output=usage[1], **more),
                raw=None, model="scripted")


def tool(name: str, n: int = 1, usage: tuple[int, int] = (10, 2), more: dict | None = None, **inp: Any) -> Turn:
    return Turn(blocks=[{"type": "tool_call", "id": f"c{n}_{name}", "name": name, "input": inp}], stop=TOOL_USE,
                usage=Usage(input=usage[0], output=usage[1], **(more or {})), raw=None, model="scripted")


@dataclass
class Call:
    model: dict
    key: str | None
    req: Request

    def last_user_text(self) -> str:
        for m in reversed(self.req.messages):
            if m["role"] == "user":
                return "\n".join(b.get("text") or str(b.get("content") or "") for b in m["content"])
        return ""


class ScriptedModel:
    def __init__(self, script):
        self.script = script
        self.runs: list = []
        self.calls: list[Call] = []

    def _next(self, req: Request) -> Turn:
        n = len(self.calls)
        s = self.script
        if callable(s):
            return s(req, n)
        if isinstance(s, str):
            return answer(s)
        if isinstance(s, list) and s:
            return s.pop(0)
        return answer("done")

    def resolve(self, model: dict, key: str | None):
        sm = self

        class Provider:
            name = "openai"

            def __init__(self):
                self.model = model.get("model")

            async def turn(self, req: Request, emit):
                sm.calls.append(Call(dict(model), key, req))
                nxt = sm._next(req)
                if isinstance(nxt, Exception):
                    raise nxt
                for b in nxt.blocks:
                    if b["type"] == "text":
                        emit("delta", b["text"])
                return nxt

        return Provider()

    def runs_of(self, step: str) -> list:
        """The runs of one flow step (its prompt names the step)."""
        return [r for r in self.runs if f"Step: {step} (" in r.prompt]


def scripted(monkeypatch, script=None) -> ScriptedModel:
    monkeypatch.setenv("KEEL_FAKE", "0")
    sm = ScriptedModel(script)
    monkeypatch.setattr(providers, "resolve", sm.resolve)
    real_run = HarnessRunner.run

    async def run(self, req, emit):
        sm.runs.append(req)
        return await real_run(self, req, emit)

    monkeypatch.setattr(HarnessRunner, "run", run)
    return sm
