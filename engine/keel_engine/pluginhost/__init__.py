"""keel's plugin host, engine side (docs/plugins/07-step1-contract.md).

It reads the plugins that came in the image and the ones a person installed, checks them, puts them in dependency
order and writes run/resolved.json and run/env for keel-start. `keel-engine plugins …` is its command line (cli.py).
Nothing here imports the FastAPI app: the command line must be fast and have no side effects.
"""

from __future__ import annotations

SDK = 1   # the plugin SDK major this keel offers; a plugin's requires.sdk must equal it


class PluginError(Exception):
    """A problem that stops a plugin command (a bad installed.json, a refused package): the message says why."""
