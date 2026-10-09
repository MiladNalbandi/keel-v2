"""The hello test plugin's engine part (e2e/marketplace): one route, GET /hello/ping, and its content (a workflow and an
agent) through ADDON's content folder. keel loads it as an add-on (KEEL_PLUGIN_ADDONS=keel_plugin_hello, written by
`keel-engine plugins resolve`)."""

from pathlib import Path

from fastapi import APIRouter

# the same version as ../../keel-plugin.yml (the e2e writes both)
VERSION = "1.0.0"

router = APIRouter()


@router.get("/hello/ping")
async def ping():
    return {"pong": "hello", "version": VERSION}


ADDON = {
    "name": "hello",
    "version": VERSION,
    "content": Path(__file__).resolve().parents[2] / "content",
    "router": router,
}
