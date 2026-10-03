"""`keel-engine`: serve the engine with uvicorn on KEEL_ENGINE_HOST:KEEL_ENGINE_PORT (127.0.0.1:8090)."""

import logging

import uvicorn

from . import config
from .app import create_app


def run():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(create_app(), host=config.host(), port=config.port(), log_level="info")


if __name__ == "__main__":
    run()
