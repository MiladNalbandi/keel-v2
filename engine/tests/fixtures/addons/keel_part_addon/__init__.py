"""An add-on that brings every kind of piece a part can have, for the registry's tests (tests/test_extensions.py).
It is switched on per project like Database, Git and CI/CD, and records what core calls in SEEN."""

from keel_engine.runtime.actions import ActionResult

SEEN: list[tuple] = []


class AcmeError(Exception):
    def __init__(self, status: int, message: str, hint: str = ""):
        super().__init__(message)
        self.status = status
        self.hint = hint


def ping(a):
    return ActionResult(True, f"pong: {a.params.get('say')}")


def _router():
    from fastapi import APIRouter

    router = APIRouter()

    @router.get("/acme/boom")
    async def boom():
        raise AcmeError(409, "Too loud.", "Whisper.")

    return router


def call(c, tool, args):
    return f"{tool} in {c['project']}: {args.get('what', 'all')}"


def keel_mcp(server, api, guard, write):
    if write:
        @server.tool(structured_output=False)
        def keel_acme_poke() -> str:
            """Acme: poke it."""
            return "poked"
    else:
        @server.tool(structured_output=False)
        def keel_acme_look() -> str:
            """Acme: look at it."""
            return "looked"


def on_scan(root, pid):
    SEEN.append(("scan", pid))
    return {"acme": {"seen": pid}}


def on_commit(root):
    SEEN.append(("commit", root))


def on_thread_start(root, thread):
    SEEN.append(("thread", root, thread))


def prompt_context(agent, root, pid, query):
    return f"Acme says hi to {agent.name}." if pid else ""


def mcp_specs(agent, root):
    return [{"name": "acme-extra", "command": "true"}]


def pr_body_sections(thread_id):
    return ["## Acme", "", f"Thread {thread_id}.", ""]


ADDON = {
    "name": "acme",
    "title": "Acme",
    "version": "0.0.1",
    "requires": ">=0.1.0",
    "per_project": True,
    "actions": {"acme:ping": ping},
    "params": {"acme:ping": {"say": "required"}},
    "docs": {"acme:ping": {"summary": "Acme: answers pong.", "steps": ["with: say."]}},
    "read_tools": ["acme_look"],
    "mcp": {"server": "keel-acme", "module": "keel_part_addon.server", "args": ["x"], "call": call},
    "router": _router,
    "errors": AcmeError,
    "keelbot": {"prompt": "The Acme plugin is on.", "actions": ["acme:ping  {say}"]},
    "keel_mcp": keel_mcp,
    "hooks": {"on_scan": on_scan, "on_commit": on_commit, "on_thread_start": on_thread_start,
              "prompt_context": prompt_context, "mcp_specs": mcp_specs, "pr_body_sections": pr_body_sections},
}
