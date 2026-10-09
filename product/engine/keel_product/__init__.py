"""keel Product, the engine side of the add-on: its workflows and agents (product/content), its code actions
(actions.py), its fake-model answers (fake.py). Loaded only when KEEL_ADDONS or KEEL_PLUGIN_ADDONS names keel_product
(keel_engine/addons.py).

CONTENT is two folders up from this package, in both layouts:

    repo:    product/engine/keel_product/   →  product/content
    plugin:  <plugin>/engine/keel_product/  →  <plugin>/content   (product/keel-plugin.yml, build-plugin.sh)
"""

from __future__ import annotations

import os
from pathlib import Path

from .actions import ACTIONS
from .fake import answer as fake_answer
from .routes import router

# Keep equal to version in ../../keel-plugin.yml and PRODUCT_VERSION in the api (tests/test_manifest.py).
VERSION = "0.1.0-beta.1"
CONTENT = Path(os.environ.get("KEEL_PRODUCT_CONTENT") or Path(__file__).resolve().parents[2] / "content")

ADDON = {
    "name": "product",
    "version": VERSION,
    # the same as requires.keel in keel-plugin.yml
    "requires": ">=0.15.0,<1.0.0",
    "content": CONTENT,
    "actions": ACTIONS,
    "fake": fake_answer,
    "router": router,
}
