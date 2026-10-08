"""A tiny add-on for the engine's add-on tests (tests/test_addons.py): one workflow, one agent, one action, fake answers
and one route."""

from pathlib import Path

from fastapi import APIRouter

from keel_engine.runtime.actions import ActionResult

router = APIRouter()


@router.get("/demo/ping")
async def ping():
    return {"pong": True}


def echo(a):
    a.event("demo.saved", {"items": len(a.data.get("folders") or []), "answers": a.data.get("answers_clarify"),
                           "read": a.data.get("read_results")})
    return ActionResult(True, f"echo: {a.params.get('say', 'hi')}")


def fake(req):
    if req.agent != "demo-agent":
        return None
    if req.step_name == "ask first":
        if "Answers to your questions:" in (req.feedback or ""):
            return None, "", f"DONE with {req.feedback.splitlines()[1].strip()}", {}
        block = '```keel-questions\n[{"id": "colour", "question": "Which colour?", "options": ["Blue", "Green"]}]\n```'
        return None, "", "I need one answer first.\n" + block, {}
    return None, "", f"READ {req.root} readonly={getattr(req.toolbox, 'readonly', False)}", {}


ADDON = {
    "name": "demo",
    "version": "0.0.1",
    "requires": ">=0.1.0",
    "content": Path(__file__).parent / "content",
    "actions": {"demo:echo": echo},
    "fake": fake,
    "router": router,
}
