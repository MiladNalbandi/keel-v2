"""`needs_plugins` (docs/plugins/13-step4-contract.md §8): a workflow names the plugins it needs, and keel refuses to
start it while one of them is not loaded. The api then opens a plugin-install request for each missing one."""

from __future__ import annotations

from .. import extensions
from . import MarketError, catalog, local


def loaded_names() -> set[str]:
    """The plugins this engine runs with: the ones keel's last start loaded (run/resolved.json, any part) and the
    engine parts it has (add-ons, KEEL_ADDONS)."""
    return set(local.loaded() or {}) | {p.name for p in extensions.parts()}


def title(name: str) -> str:
    """A plugin's title: from what keel has, else from a catalog, else its name."""
    h = local.have().get(name)
    if h and h.manifest:
        return h.title
    found = catalog.lookup(name)
    return found[1].title if found else name


def missing(names: list[str] | None) -> list[str]:
    have = loaded_names()
    return [n for n in names or [] if n not in have]


def _and(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def check(workflow_id: str, names: list[str] | None) -> None:
    """Raise MarketError 409 {error, hint, missing, workflow} when the workflow needs a plugin that is not loaded."""
    gone = missing(names)
    if gone:
        raise MarketError(409, f"This workflow needs {_and([title(n) for n in gone])}.",
                          "Install it in Control › Plugins, then restart keel.", missing=gone, workflow=workflow_id)
