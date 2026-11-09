"""The risk tier of a tool call (from keel-gate)."""

from __future__ import annotations

from enum import IntEnum


class Tier(IntEnum):
    """How bad the worst outcome of a call is."""

    T0 = 0  # reads only: nothing changes
    T1 = 1  # changes something that can be undone (a file in the project, a commit)
    T2 = 2  # hard to undo: a person should decide (a push, an install, a deploy)
    T3 = 3  # catastrophic: never runs (rm -rf /, DROP TABLE, a disk format)

    @property
    def words(self) -> str:
        return TIER_WORDS[self]


TIER_WORDS = {
    Tier.T0: "read only",
    Tier.T1: "can be undone",
    Tier.T2: "hard to undo",
    Tier.T3: "cannot be undone",
}
