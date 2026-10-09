"""`keel-engine`: serve the engine with uvicorn on KEEL_ENGINE_HOST:KEEL_ENGINE_PORT (127.0.0.1:8090).

`keel-engine plugins <resolve|install|list|set|search|get|update|rollback|remove> …` runs the plugin host's command
line instead (pluginhost/cli.py).
It does not import the FastAPI app, so it is fast and starts nothing.
"""

import logging
import sys


def serve():
    import uvicorn

    from . import config
    from .app import create_app

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(create_app(), host=config.host(), port=config.port(), log_level="info")


def run(argv: list[str] | None = None):
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] == "plugins":
        from .pluginhost.cli import main

        raise SystemExit(main(args[1:]))
    serve()


if __name__ == "__main__":
    run()
