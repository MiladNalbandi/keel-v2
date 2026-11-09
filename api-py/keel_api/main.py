"""`keel-api`: starts the Python api with uvicorn (docker/keel-start runs it in front of the Kotlin api)."""

from __future__ import annotations

import logging

import uvicorn

from .app import create_app
from .settings import Settings


def run() -> None:
    settings = Settings()
    if not settings.internal_token:
        logging.getLogger("keel_api").warning(
            "KEEL_INTERNAL_TOKEN is empty: the MCP tokens of this api and of the Kotlin api will not match"
        )
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        # never rewrite the client's address from X-Forwarded-For: LocalAuth decides whom to trust (ClientIp)
        proxy_headers=False,
        server_header=False,
        log_level="warning",
        # the live streams never end by themselves: a stop or restart closes them after 3 seconds
        timeout_graceful_shutdown=3,
    )


if __name__ == "__main__":
    run()
