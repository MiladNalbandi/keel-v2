"""keel Product, the engine side of the add-on: its workflows and agents (product/content), its code actions
(actions.py), its fake-model answers (fake.py). Loaded only when KEEL_ADDONS names keel_product (keel_engine/addons.py).
"""

from __future__ import annotations

import os
from pathlib import Path

from .actions import ACTIONS
from .fake import answer as fake_answer
from .routes import router

VERSION = "0.1.0-beta.1"
CONTENT = Path(os.environ.get("KEEL_PRODUCT_CONTENT") or Path(__file__).resolve().parents[2] / "content")

ADDON = {
    "name": "product",
    "version": VERSION,
    "requires": ">=0.13.0,<0.15.0",
    "content": CONTENT,
    "actions": ACTIONS,
    "fake": fake_answer,
    "router": router,
}
