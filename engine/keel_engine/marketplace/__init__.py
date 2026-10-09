"""keel's marketplace client, engine side (docs/plugins/13-step4-contract.md): find a plugin in a signed catalog, see
what it may do, install, update, roll back or remove it in a running keel. Core: it never imports a plugin.

    signing.py   minisign Ed25519: verify a catalog or a package, sign (keel's own tools and the tests)
    sources.py   the catalogs keel reads (sources.json) and the person's rules (rules.json)
    catalog.py   fetch, verify and cache each catalog's index; search it
    install.py   plan, install, update, roll back, remove, turn on or off; what waits for a restart
    routes.py    the engine routes the api calls (/marketplace/*)

Nothing here runs a plugin's code: installing only unpacks files, and they load at the next start.
"""

from __future__ import annotations


class MarketError(Exception):
    """A refusal in plain words: the routes answer {"error", "hint", ...more} with this status, the CLI prints it."""

    def __init__(self, status: int, message: str, hint: str = "", **more):
        super().__init__(message)
        self.status = status
        self.hint = hint
        self.more = more

    def body(self) -> dict:
        out: dict = {"error": str(self)}
        if self.hint:
            out["hint"] = self.hint
        out.update(self.more)
        return out
